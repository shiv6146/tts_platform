#!/usr/bin/env python3
"""gRPC TTS inference: vLLM or llama.cpp tokens + optimized SNAC pipeline."""

from __future__ import annotations

import logging
import os
import sys
from concurrent import futures

import grpc

from engine import initialize, ready, synthesize_pcm_stream
from pipeline.decoder import SAMPLE_RATE

GRPC_PORT = os.environ.get("GRPC_PORT", "50051")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("inference")


def _validate_startup():
    if os.environ.get("REQUIRE_REAL_INFERENCE", "true").lower() in ("1", "true", "yes"):
        if os.environ.get("INFERENCE_MOCK", "false").lower() in ("1", "true", "yes"):
            log.error("INFERENCE_MOCK=true is not allowed when REQUIRE_REAL_INFERENCE=true")
            sys.exit(1)
    if os.environ.get("ALLOW_MOCK", "false").lower() in ("1", "true", "yes"):
        return
    if os.environ.get("INFERENCE_MOCK", "false").lower() in ("1", "true", "yes"):
        log.error("INFERENCE_MOCK requires ALLOW_MOCK=true")
        sys.exit(1)


def _ensure_proto():
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from tts.v1 import inference_pb2, inference_pb2_grpc  # noqa: F401
        return inference_pb2, inference_pb2_grpc
    except ImportError:
        log.warning("protobuf stubs missing; run: make gen-proto-py")
        return None, None


class Servicer:
    def __init__(self, pb2, pb2_grpc):
        self.pb2 = pb2
        initialize()

    def Health(self, request, context):
        del request
        ok = ready()
        backend = os.environ.get("INFERENCE_BACKEND", "auto")
        if ok:
            try:
                from backends.select import _backend_kind

                if _backend_kind is not None:
                    backend = _backend_kind.value
            except Exception:
                pass
        if not ok:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("inference backend or SNAC not ready")
        return self.pb2.HealthResponse(ok=ok, backend=backend)

    def Synthesize(self, request, context):
        if not ready():
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("inference not ready")
            return
        voice = request.voice or "tara"
        seq = 0
        try:
            for pcm in synthesize_pcm_stream(
                request.text,
                voice,
                request_id=request.request_id,
            ):
                if not pcm:
                    continue
                yield self.pb2.AudioChunk(pcm=pcm, sample_rate=SAMPLE_RATE, seq=seq)
                seq += 1
        except Exception as exc:
            log.exception("Synthesize failed")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))

    def SynthesizeLive(self, request_iterator, context):
        voice = "tara"
        request_id = ""
        buffer = []
        for msg in request_iterator:
            request_id = msg.request_id or request_id
            if msg.voice:
                voice = msg.voice
            if msg.text:
                buffer.append(msg.text)
            if msg.final and buffer:
                text = "".join(buffer)
                buffer.clear()
                for chunk in self.Synthesize(
                    self.pb2.SynthesizeRequest(
                        request_id=request_id,
                        text=text,
                        voice=voice,
                    ),
                    context,
                ):
                    yield chunk


def serve():
    _validate_startup()
    pb2, pb2_grpc = _ensure_proto()
    if pb2 is None:
        raise SystemExit("missing generated protobuf stubs")

    workers = int(os.environ.get("GRPC_MAX_WORKERS", "48"))
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=workers))
    pb2_grpc.add_TTSInferenceServicer_to_server(Servicer(pb2, pb2_grpc), server)
    listen = f"[::]:{GRPC_PORT}"
    server.add_insecure_port(listen)
    log.info(
        "inference listening on %s backend=%s grpc_workers=%d",
        listen,
        os.environ.get("INFERENCE_BACKEND", "auto"),
        workers,
    )
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve()

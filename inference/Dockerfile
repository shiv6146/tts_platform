# llama.cpp client + SNAC (no vLLM). Used with llama-cpp-server sidecar (macOS / CPU).
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && rm -rf /var/lib/apt/lists/*
COPY inference/requirements-base.txt inference/requirements-llamacpp.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch==2.5.1 torchaudio==2.5.1 \
      --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements-llamacpp.txt
COPY proto /app/proto
COPY inference /app/inference
RUN mkdir -p /app/inference/tts/v1 && python -m grpc_tools.protoc -I /app/proto \
    --python_out=/app/inference --grpc_python_out=/app/inference \
    /app/proto/tts/v1/inference.proto && \
    touch /app/inference/tts/__init__.py /app/inference/tts/v1/__init__.py
ENV PYTHONPATH=/app/inference
ENV GRPC_PORT=50051
ENV INFERENCE_BACKEND=llamacpp
ENV REQUIRE_REAL_INFERENCE=true
ENV INFERENCE_MOCK=false
ENV LLAMACPP_URL=http://llama-cpp-server:5006/v1/completions
ENV ORPHEUS_GGUF_MODEL=Orpheus-3b-FT-Q8_0.gguf
ENV SNAC_DEVICE=cpu
EXPOSE 50051
CMD ["python", "/app/inference/server.py"]

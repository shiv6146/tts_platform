#!/usr/bin/env bash
# Generate Go + Python gRPC stubs from proto/ (never edit *.pb.go by hand).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
GO_OUT_DIR="api/internal/grpc/tts/v1"

install_go_plugins() {
  go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.35.1
  go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1
}

run_protoc_go() {
  export PATH="${PATH}:$(go env GOPATH 2>/dev/null || echo /go)/bin"
  protoc --go_out=. --go_opt=module=github.com/tts-platform/api \
    --go-grpc_out=. --go-grpc_opt=module=github.com/tts-platform/api \
    -I proto proto/tts/v1/inference.proto
}

relocate_go_stubs() {
  mkdir -p "${GO_OUT_DIR}"
  rm -f "${GO_OUT_DIR}"/*.pb.go
  if [[ -d internal/grpc/tts/v1 ]]; then
    mv internal/grpc/tts/v1/*.pb.go "${GO_OUT_DIR}/"
    rm -rf internal/grpc
  elif [[ -d api/tts/v1 ]]; then
    mv api/tts/v1/*.pb.go "${GO_OUT_DIR}/"
    rm -rf api/tts
  else
    echo "protoc did not emit Go stubs under internal/grpc or api/tts" >&2
    exit 1
  fi
}

gen_go_local() {
  command -v protoc >/dev/null || return 1
  install_go_plugins
  run_protoc_go
  relocate_go_stubs
}

gen_go_docker() {
  docker run --rm \
    -v "${ROOT}:/work" -w /work \
    golang:1.23-bookworm \
    bash -euxo pipefail -c '
      apt-get update -qq && apt-get install -y -qq protobuf-compiler
      go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.35.1
      go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1
      export PATH="$PATH:$(go env GOPATH)/bin"
      protoc --go_out=. --go_opt=module=github.com/tts-platform/api \
        --go-grpc_out=. --go-grpc_opt=module=github.com/tts-platform/api \
        -I proto proto/tts/v1/inference.proto
      mkdir -p api/internal/grpc/tts/v1
      rm -f api/internal/grpc/tts/v1/*.pb.go
      mv internal/grpc/tts/v1/*.pb.go api/internal/grpc/tts/v1/
      rm -rf internal/grpc api/tts
    '
}

gen_py_local() {
  python3 -m grpc_tools.protoc -I proto \
    --python_out=inference \
    --grpc_python_out=inference \
    proto/tts/v1/inference.proto
}

gen_py_docker() {
  docker run --rm \
    -v "${ROOT}:/work" -w /work \
    python:3.11-slim \
    bash -euxo pipefail -c '
      pip install -q grpcio-tools
      python -m grpc_tools.protoc -I proto \
        --python_out=inference \
        --grpc_python_out=inference \
        proto/tts/v1/inference.proto
    '
}

echo "==> Go stubs -> ${GO_OUT_DIR}"
if gen_go_local 2>/dev/null; then
  echo "    (local protoc)"
else
  echo "    (docker golang:1.23-bookworm)"
  gen_go_docker
fi

echo "==> Python stubs -> inference/tts/v1"
mkdir -p inference/tts/v1
if gen_py_local 2>/dev/null; then
  echo "    (local grpc_tools)"
else
  echo "    (docker python:3.11-slim)"
  gen_py_docker
fi
touch inference/tts/__init__.py inference/tts/v1/__init__.py
echo "Done. Contract source: proto/tts/v1/inference.proto"

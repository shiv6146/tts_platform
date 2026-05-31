.PHONY: gen-api gen-proto gen-proto-py sync-openapi tidy gen-web build-web build-api

sync-openapi:
	cp api/openapi.yaml api/internal/docs/openapi.yaml

gen-api: sync-openapi
	cd api && go run github.com/oapi-codegen/oapi-codegen/v2/cmd/oapi-codegen@v2.4.1 \
		-generate types,chi-server,spec -package gen -o internal/gen/openapi.go openapi.yaml

gen-proto:
	protoc --go_out=. --go_opt=module=github.com/tts-platform/api \
		--go-grpc_out=. --go-grpc_opt=module=github.com/tts-platform/api \
		-I proto proto/tts/v1/inference.proto
	mv api/tts/v1/*.pb.go api/internal/grpc/tts/v1/ 2>/dev/null || true

gen-proto-py:
	mkdir -p inference/tts/v1
	python -m grpc_tools.protoc -I proto \
		--python_out=inference \
		--grpc_python_out=inference \
		proto/tts/v1/inference.proto
	touch inference/tts/__init__.py inference/tts/v1/__init__.py

gen-web:
	cd web && bun install && bun run generate:api

build-web: gen-web
	cd web && bun run build
	rm -rf api/internal/ui/dist && cp -r web/dist api/internal/ui/dist

build-api: build-web gen-api
	cd api && go build -o ../bin/api ./cmd/api

tidy:
	cd api && go mod tidy
	cd metering && go mod tidy

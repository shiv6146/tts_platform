.PHONY: gen-api gen-proto gen-proto-py gen-contracts sync-openapi tidy gen-web build-web build-api

sync-openapi:
	cp api/openapi.yaml api/internal/docs/openapi.yaml

gen-api: sync-openapi
	cd api && go run github.com/oapi-codegen/oapi-codegen/v2/cmd/oapi-codegen@v2.4.1 \
		-generate types,chi-server,spec -package gen -o internal/gen/openapi.go openapi.yaml

# Regenerate gRPC stubs from proto/ (Go + Python). Uses Docker if protoc is not installed.
gen-proto gen-proto-py gen-contracts:
	chmod +x scripts/gen-proto.sh
	./scripts/gen-proto.sh

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

validate-metering:
	python3 scripts/validate_metering.py

debug-pcm:
	python3 scripts/debug_pcm_stream.py --grpc

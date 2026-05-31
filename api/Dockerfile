FROM golang:1.23-alpine AS build
ENV GOTOOLCHAIN=auto
WORKDIR /src
COPY api/go.mod api/go.sum* ./
RUN go mod download 2>/dev/null || true
COPY api/ ./
RUN go mod tidy && CGO_ENABLED=0 go build -o /api ./cmd/api

FROM alpine:3.20
RUN apk add --no-cache ca-certificates wget
COPY --from=build /api /api
EXPOSE 8080
CMD ["/api"]

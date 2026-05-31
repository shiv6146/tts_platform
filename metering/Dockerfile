FROM golang:1.23-alpine AS build
ENV GOTOOLCHAIN=auto
WORKDIR /src
COPY metering/go.mod metering/go.sum* ./
RUN go mod download 2>/dev/null || true
COPY metering/ ./
RUN go mod tidy && CGO_ENABLED=0 go build -o /metering ./cmd/metering

FROM alpine:3.20
RUN apk add --no-cache ca-certificates
COPY --from=build /metering /metering
EXPOSE 8081
CMD ["/metering"]

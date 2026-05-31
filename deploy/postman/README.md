# Postman & public ports

## Import collection

1. Postman → **Import** → `deploy/postman/tts_platform.postman_collection.json`
2. Collection variables:
   - `baseUrl` — e.g. `https://your-lightning-host:8080`
   - `username` / `password` — credentials for **Register** or **Login**
   - `apiKey` — set automatically when you run **Register**, **Login**, or **Create API key**

OpenAPI source: `api/openapi.yaml` · Swagger UI: `{baseUrl}/docs`

### Per-user rate limits

| Method | Path | Auth |
|--------|------|------|
| GET | `/v1/account/rate-limit` | User API key |
| GET | `/v1/admin/users/{userId}/rate-limit` | `PLATFORM_ADMIN_KEY` (`X-Admin-Key` or Bearer) |
| PUT | `/v1/admin/users/{userId}/rate-limit` | Admin — body `{"rpm":120,"rph":5000,"rpd":50000}` |
| DELETE | `/v1/admin/users/{userId}/rate-limit` | Admin — clears override, restores env defaults |

Global defaults: `RATE_LIMIT_RPM`, `RATE_LIMIT_RPH`, `RATE_LIMIT_RPD` in `.env`.

## Ports to expose publicly

Current `docker-compose.yml` only publishes some services on the host. For Lightning (or any cloud), expose what you need:

| Port | Service | Purpose | Expose publicly? |
|------|---------|---------|------------------|
| **8080** | `api` | REST TTS, auth, wallet, **`/metrics`** | **Yes** — main API |
| **3000** | `grafana` | Dashboards UI | **Yes** — if you want Grafana (add mapping below) |
| **9090** | `prometheus` | PromQL UI & raw metrics | **Yes** — optional; Grafana usually enough |
| **8081** | `metering` | Billing worker **`/metrics`** | Optional (Prometheus scrapes it internally) |
| 50051 | `inference` | gRPC only | **No** — internal |
| 5432 | `postgres` | DB | **No** |
| 6379 | `valkey` | Cache | **No** |
| 4222 / 8222 | `nats` | Messaging / monitor | **No** |

Grafana and Prometheus have **no host ports** in compose by default (to avoid conflicts on Lightning). Add to `docker-compose.yml`:

```yaml
  prometheus:
    ports:
      - "9090:9090"

  grafana:
    ports:
      - "3000:3000"
```

Then open in the studio / Lightning port panel:

- Grafana: `http://<host>:3000`
- Prometheus: `http://<host>:9090`
- API metrics: `http://<host>:8080/metrics`

## Default credentials

| What | Username / key | Password / secret | Notes |
|------|----------------|-------------------|--------|
| **Grafana** | `admin` | `admin` | Set in `docker-compose.yml` (`GF_SECURITY_ADMIN_*`). Change in production. |
| **Bootstrap API user** | `dev` | `devpassword` | From `.env` (`DEFAULT_USERNAME` / `DEFAULT_PASSWORD`). Used only at first DB seed. |
| **API access** | — | `sk-...` | **Not** the dev password. Get from `docker compose logs api` line `default API key (save now): sk-...` or create via **Register** + **Create API key**. |
| **Postgres** | `tts` | `tts` | Internal only; do not expose 5432. |

Prometheus has **no auth** by default — do not expose 9090 on the public internet without a reverse proxy or network ACL.

## Voices

Orpheus supports voices such as: `tara`, `leah`, `jess`, `leo`, `dan`, `mia`, `zac`, `zoe` (see Orpheus docs; default in API is `tara`).

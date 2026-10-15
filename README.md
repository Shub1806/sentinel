# Sentinel

An AI on-call agent for Kubernetes. When an alert fires, Sentinel investigates logs, metrics,
Kubernetes events and recent deploys, identifies the root cause, proposes a fix that only runs
after human approval, and drafts the post-mortem.

> 🚧 Work in progress.

## Planned stack

- **Services:** orders (Java / Spring Boot), payments (Python / FastAPI), notifications (Node.js / TypeScript)
- **Platform:** Kubernetes (k3d), Helm, Terraform
- **Streaming:** Redpanda (Kafka API)
- **AWS services (emulated with LocalStack):** S3, SQS, Secrets Manager
- **Observability:** Prometheus, Grafana, Loki, OpenTelemetry, Jaeger
- **Agent:** Python, local LLM via Ollama, read-only tools + approval-gated actions


## Local development

Requirements: Docker Desktop.

```bash
cp .env.example .env    # then set a real POSTGRES_PASSWORD
docker compose up -d    # starts Postgres, Redpanda and Redpanda Console
```

| Service          | Address on your machine   |
|------------------|---------------------------|
| Postgres         | `localhost:15432`         |
| Redpanda (Kafka) | `localhost:19092`         |
| Redpanda Console | http://localhost:8090     |

Stop with `docker compose down` (keeps data) or `docker compose down -v` (deletes data).
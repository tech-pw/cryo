FROM golang:1.27.2-bookworm@sha256:55395706e9703db746cc507abfc4eb2aea75918f8a8024e4848e2cb81004f5ad AS cryo-engine-go-builder

WORKDIR /build
COPY engine-go/go.mod engine-go/go.sum ./
# go.mod replaces apache/iceberg-go with this local patched copy, so it must be present
# before `go mod download` -- otherwise resolution fails even for an unrelated dependency.
COPY engine-go/third_party ./third_party
RUN go mod download
COPY engine-go/ ./
RUN CGO_ENABLED=0 go build -o cryo-engine-go ./cmd/cryo-engine-go

FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install the exact hashed production graph exported from uv.lock. Regenerate with
# `uv export --frozen --no-dev --no-emit-project --no-header --format requirements-txt`.
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes --requirement requirements.lock

# Copy application code
COPY . .

# Native maintenance binary built in the stage above.
COPY --from=cryo-engine-go-builder /build/cryo-engine-go /app/engine-go/bin/cryo-engine-go
RUN ln -sf /app/engine-go/bin/cryo-engine-go /app/engine-go/bin/cryo-engine
ENV PATH="/app/engine-go/bin:${PATH}"
RUN chmod +x /app/engine-go/bin/cryo-engine-go

# Install Cryo without re-resolving the locked dependency graph.
RUN pip install --no-cache-dir --no-deps .

# UID/GID 10001 must match securityContext.runAsUser/runAsGroup in the Helm chart.
# .local/ holds the relative-path defaults (puffin, observation lakehouse) under WORKDIR.
RUN groupadd --system --gid 10001 cryo \
    && useradd --system --uid 10001 --gid cryo --create-home --home-dir /home/cryo cryo \
    && mkdir -p /app/.local \
    && chown cryo:cryo /app/.local
USER 10001:10001

# Default command (can be overridden in docker-compose)
CMD ["uvicorn", "cryo.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

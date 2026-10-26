FROM golang:1.27.3-bookworm@sha256:21ade014faf952365b9ecbd8dbdb87176fbacb82fac9fb132d9f2a7b8cc3f132 AS cryo-engine-go-builder

WORKDIR /build
COPY engine-go/go.mod engine-go/go.sum ./
# go.mod replaces apache/iceberg-go with this local patched copy, so it must be present
# before `go mod download` -- otherwise resolution fails even for an unrelated dependency.
COPY engine-go/third_party ./third_party
RUN go mod download
COPY engine-go/ ./
RUN CGO_ENABLED=0 go build -o cryo-engine-go ./cmd/cryo-engine-go

# Confirmed live, 2026-10-12: the prior digest (debian 13.6, 2026-era base rebuild) had
# 40 fixable HIGH OS-package CVEs (bsdutils, gzip, lib{blkid1,mount1,pcre2-8-0,lastlog2-2})
# purely from a stale base layer, unrelated to anything this app pins itself.
FROM python:3.12-slim@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe

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

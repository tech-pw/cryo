FROM rust:1-bookworm AS cryo-engine-builder

WORKDIR /build
COPY cryo-engine/Cargo.toml cryo-engine/Cargo.lock* ./
COPY cryo-engine/src ./src
RUN cargo build --release

FROM golang:1.25-bookworm AS cryo-engine-go-builder

WORKDIR /build
COPY engine-go/go.mod engine-go/go.sum ./
RUN go mod download
COPY engine-go/ ./
RUN CGO_ENABLED=0 go build -o cryo-engine-go ./cmd/cryo-engine-go

FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Native maintenance binaries built in the stages above.
COPY --from=cryo-engine-builder /build/target/release/cryo-engine /app/cryo-engine/target/release/cryo-engine
COPY --from=cryo-engine-go-builder /build/cryo-engine-go /app/engine-go/bin/cryo-engine-go
RUN ln -sf /app/engine-go/bin/cryo-engine-go /app/engine-go/bin/cryo-engine
ENV PATH="/app/engine-go/bin:/app/cryo-engine/target/release:${PATH}"
RUN chmod +x /app/cryo-engine/target/release/cryo-engine /app/engine-go/bin/cryo-engine-go

# Install Cryo in development mode
RUN pip install -e .

# Default command (can be overridden in docker-compose)
CMD ["uvicorn", "cryo.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

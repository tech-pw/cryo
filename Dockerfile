FROM rust:1-bookworm AS cryo-engine-builder

WORKDIR /build
COPY cryo-engine/Cargo.toml cryo-engine/Cargo.lock* ./
COPY cryo-engine/src ./src
RUN cargo build --release

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

# Native maintenance binary built in the Rust stage above.
COPY --from=cryo-engine-builder /build/target/release/cryo-engine /app/cryo-engine/target/release/cryo-engine
ENV PATH="/app/cryo-engine/target/release:${PATH}"
RUN chmod +x /app/cryo-engine/target/release/cryo-engine

# Install Cryo in development mode
RUN pip install -e .

# Default command (can be overridden in docker-compose)
CMD ["uvicorn", "cryo.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

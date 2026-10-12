FROM python:3.12-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYSPARK_PYTHON=python3

RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless curl tini \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir \
    "pyspark>=3.5.0,<4.0.0" \
    "pandas>=2.0.0" \
    "pyarrow>=15.0.0"

RUN useradd --create-home --uid 10001 --user-group spark \
    && mkdir -p /workspace \
    && chown spark:spark /workspace
WORKDIR /workspace
USER 10001
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash", "-lc", "sleep infinity"]

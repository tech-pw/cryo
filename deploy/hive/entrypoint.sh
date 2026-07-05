#!/bin/bash
set -euo pipefail

export HIVE_CONF_DIR="${HIVE_CONF_DIR:-/opt/hive/conf}"
shopt -s nullglob
aws_jars=(/opt/hive/lib/hadoop-aws-*.jar /opt/hive/lib/aws-java-sdk-bundle-*.jar)
export HADOOP_CLASSPATH
HADOOP_CLASSPATH=$(IFS=:; echo "${aws_jars[*]}")
export HADOOP_CLIENT_OPTS="${HADOOP_CLIENT_OPTS:-} -Xmx1G"

wait_for_port() {
  local host="$1"
  local port="$2"
  until bash -c "exec 3<>/dev/tcp/${host}/${port}" 2>/dev/null; do
    sleep 2
  done
}

if [[ "${SERVICE_NAME:-}" != "metastore" ]]; then
  echo "Only metastore service is supported in this image override" >&2
  exit 1
fi

echo "Waiting for HMS Postgres at ${HMS_POSTGRES_HOST:-hms-postgres}:5432"
wait_for_port "${HMS_POSTGRES_HOST:-hms-postgres}" 5432

if ! /opt/hive/bin/schematool -dbType postgres -info >/dev/null 2>&1; then
  echo "Initializing Hive Metastore schema"
  /opt/hive/bin/schematool -dbType postgres -initSchema
fi

echo "Starting Hive Metastore"
exec /opt/hive/bin/hive --service metastore

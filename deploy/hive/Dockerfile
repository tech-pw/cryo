FROM apache/hive:4.0.0

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && curl -fsSL https://jdbc.postgresql.org/download/postgresql-42.7.4.jar \
      -o /opt/hive/lib/postgresql.jar \
    && cp /opt/hadoop/share/hadoop/tools/lib/hadoop-aws-*.jar /opt/hive/lib/ \
    && cp /opt/hadoop/share/hadoop/tools/lib/aws-java-sdk-bundle-*.jar /opt/hive/lib/ \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/hive/hive-site.xml /opt/hive/conf/hive-site.xml
COPY deploy/hive/entrypoint.sh /opt/hive/conf/entrypoint.sh
RUN chmod +x /opt/hive/conf/entrypoint.sh

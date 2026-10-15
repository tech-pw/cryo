FROM apache/hive:4.0.0

USER root

# apache/hive:4.0.0 is Debian 11. Point-release debs have left the live mirror;
# rewrite sources onto archive.debian.org or apt-get install 404s. Check-Valid-Until
# is required because archived Release files are expired by design.
RUN find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -print0 \
      | xargs -0 -r sed -i \
        -e 's|https\?://deb.debian.org/debian|http://archive.debian.org/debian|g' \
        -e 's|https\?://security.debian.org/debian-security|http://archive.debian.org/debian-security|g' \
        -e 's|https\?://deb.debian.org/debian-security|http://archive.debian.org/debian-security|g' \
    && find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -print0 \
      | xargs -0 -r sed -i '/bullseye-updates/d' \
    && apt-get update -o Acquire::Check-Valid-Until=false \
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

USER hive

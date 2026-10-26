#!/usr/bin/env bash
# Control-plane resource sampler (the cluster has no metrics-server). Every INTERVAL seconds,
# reads cumulative CPU usage (cgroup v2 cpu.stat usage_usec) and memory.current from each Cryo
# control-plane pod via read-only `kubectl exec cat`, and appends JSON lines to OUT.
set -u
CTX=infra:<benchmark-cluster>
NS=cryo-bench
OUT=${1:?output file}
INTERVAL=${INTERVAL:-15}
while true; do
  for pod in $(kubectl --context $CTX -n $NS get pods -o name | grep -E 'pod/(cryo-bench-(api|worker|governor|scanner-worker|scheduler)-|cryo-postgres|cryo-redis|iceberg-rest)' | grep -v migrate); do
    p=${pod#pod/}
    v=$(kubectl --context $CTX -n $NS exec "$p" -- sh -c 'sed -n 1p /sys/fs/cgroup/cpu.stat; cat /sys/fs/cgroup/memory.current' 2>/dev/null | tr '\n' ' ')
    [ -n "$v" ] && echo "{\"t\": $(date +%s), \"pod\": \"$p\", \"cpu_usage_usec\": $(echo $v | awk '{print $2}'), \"mem_bytes\": $(echo $v | awk '{print $3}')}" >> "$OUT"
  done
  sleep "$INTERVAL"
done

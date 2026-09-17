#!/bin/bash
set -euo pipefail
pkill -f "port-forward.*cryo-api.*18000" 2>/dev/null || true
sleep 1
kubectl port-forward -n cryo svc/cryo-api 18000:8000 > /tmp/pf_scorecard.log 2>&1 &
PF_PID=$!
sleep 3
TOKEN=$(curl -sS -X POST http://127.0.0.1:18000/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"subject":"scorecard","role":"viewer"}' | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
VITALS=$(curl -sS http://127.0.0.1:18000/api/v1/vitals -H "Authorization: Bearer $TOKEN")
TRENDS=$(curl -sS "http://127.0.0.1:18000/api/v1/dashboard/fleet-trends?days=7" -H "Authorization: Bearer $TOKEN")
kill $PF_PID 2>/dev/null || true
echo "=== VITALS ==="
echo "$VITALS" | python3 -m json.tool
echo "=== TRENDS (last day) ==="
echo "$TRENDS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d['health_trend'][-1], indent=2)); print(json.dumps(d['cost_trend'][-1], indent=2))"

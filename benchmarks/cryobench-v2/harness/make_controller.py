"""Write a V2 controller Job manifest (JSON) for kubectl apply.

Usage: python make_controller.py NAME SCRIPT KEY=VALUE ... > manifest.json
"""
import json, sys

CRYO_IMAGE = "ghcr.io/tech-pw/cryo@sha256:b440406dca311463c3ba672a8b6b6d469442f23851681662984effe82df1c691"
NS = "cryo-bench"

name, script, *pairs = sys.argv[1:]
env = [{"name": k, "value": v} for k, v in (p.split("=", 1) for p in pairs)]
if any(e["name"] == "WITH_S3" for e in env):
    env += [{"name": n, "valueFrom": {"secretKeyRef": {"name": "cryo-bench-s3", "key": key}}}
            for n, key in (("AWS_ACCESS_KEY_ID", "access_key_id"), ("AWS_SECRET_ACCESS_KEY", "secret_access_key"))]
print(json.dumps({
    "apiVersion": "batch/v1", "kind": "Job",
    "metadata": {"name": name, "namespace": NS,
                 "labels": {"benchmark-run": "20261020-spark-001", "benchmark-version": "v2", "purpose": "v2-controller"}},
    "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 172800, "ttlSecondsAfterFinished": 604800,
             "template": {"metadata": {"annotations": {"karpenter.sh/do-not-disrupt": "true"}, "labels": {"benchmark-version": "v2", "v2-job": name}},
                          "spec": {"restartPolicy": "Never", "serviceAccountName": "bench-v2-controller",
                                   "containers": [{"name": "controller", "image": CRYO_IMAGE,
                                                   "command": ["python", "-u", f"/opt/v2/{script}"], "env": env,
                                                   "volumeMounts": [{"name": "code", "mountPath": "/opt/v2"}],
                                                   "resources": {"requests": {"cpu": "100m", "memory": "256Mi"},
                                                                 "limits": {"cpu": "500m", "memory": "512Mi"}}}],
                                   "volumes": [{"name": "code", "configMap": {"name": "v2-p1-code"}}]}}},
}, indent=1))

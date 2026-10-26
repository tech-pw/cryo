{{/*
Readiness + liveness probes for the background daemons (scheduler, worker, scanner-worker).
Each runs cryo/observability/health_server.py on observability.metricsPort; /healthz turns
503 after 5 consecutive failed loop ticks, or when one tick has run longer than
probe.tickStallSeconds (CRYO_DAEMON_TICK_STALL_SECONDS, see cryo.daemonStallEnv).
Usage: include "cryo.daemonProbes" (dict "root" $ "probe" .Values.scheduler.probe)
*/}}
{{- define "cryo.daemonProbes" -}}
{{- if .probe.enabled }}
readinessProbe:
  httpGet:
    path: /healthz
    port: {{ .root.Values.observability.metricsPort }}
  initialDelaySeconds: {{ .probe.initialDelaySeconds }}
  periodSeconds: {{ .probe.periodSeconds }}
  timeoutSeconds: {{ .probe.timeoutSeconds }}
  failureThreshold: {{ .probe.readinessFailureThreshold }}
livenessProbe:
  httpGet:
    path: /healthz
    port: {{ .root.Values.observability.metricsPort }}
  initialDelaySeconds: {{ .probe.initialDelaySeconds }}
  periodSeconds: {{ .probe.periodSeconds }}
  timeoutSeconds: {{ .probe.timeoutSeconds }}
  failureThreshold: {{ .probe.livenessFailureThreshold }}
{{- end }}
{{- end }}

{{/* Env entry that arms the hung-tick check behind the probes above. */}}
{{- define "cryo.daemonStallEnv" -}}
- name: CRYO_DAEMON_TICK_STALL_SECONDS
  value: {{ .tickStallSeconds | default 0 | quote }}
{{- end }}

{{- define "cryo.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "cryo.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "cryo.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "cryo.labels" -}}
helm.sh/chart: {{ include "cryo.chart" . }}
{{ include "cryo.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "cryo.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cryo.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "cryo.nodeSelector" -}}
{{/*
This cluster mixes amd64 and arm64 (Graviton) nodes. The Cryo image is built amd64-only
(the Rust/Go native-engine cross-compile isn't wired up for arm64 yet), so every pod must
be pinned to amd64 nodes or it lands on arm64 and fails with "exec format error".
*/}}
nodeSelector:
  kubernetes.io/arch: amd64
{{- end }}

{{/*
Pod-level securityContext. Pass (list $ <override dict>) — the override wins key-by-key,
so redis/web can swap in their images' own non-root UIDs.
*/}}
{{- define "cryo.podSecurityContext" -}}
{{- $root := index . 0 -}}
{{- $override := index . 1 | default dict -}}
securityContext:
  {{- toYaml (merge (deepCopy $override) (deepCopy $root.Values.podSecurityContext)) | nindent 2 }}
{{- end }}

{{- define "cryo.containerSecurityContext" -}}
securityContext:
  {{- toYaml .Values.containerSecurityContext | nindent 2 }}
{{- end }}

{{- define "cryo.imagePullSecrets" -}}
{{- if .Values.imagePullSecrets }}
imagePullSecrets:
{{- range .Values.imagePullSecrets }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end }}

{{- define "cryo.prometheusAnnotations" -}}
{{- if .Values.observability.prometheusScrape }}
prometheus.io/scrape: "true"
prometheus.io/path: "/metrics"
prometheus.io/port: {{ .Values.observability.metricsPort | quote }}
{{- end }}
{{- end }}

{{- define "cryo.imageRef" -}}
{{- if .digest -}}
{{ printf "%s@%s" .repository .digest }}
{{- else -}}
{{ printf "%s:%s" .repository .tag }}
{{- end -}}
{{- end }}

{{/*
Image ref for the migration job. Follows the API image unless migration.image.repository or
migration.image.digest is set, so setting image.digest pins migrations too (issue #140).
*/}}
{{- define "cryo.migrationImageRef" -}}
{{- $api := .Values.image -}}
{{- $m := .Values.migration.image | default dict -}}
{{- if $m.repository -}}
{{- include "cryo.imageRef" (dict "repository" $m.repository "digest" $m.digest "tag" (default $api.tag $m.tag)) -}}
{{- else if $m.digest -}}
{{- include "cryo.imageRef" (dict "repository" $api.repository "digest" $m.digest "tag" $api.tag) -}}
{{- else -}}
{{- include "cryo.imageRef" $api -}}
{{- end -}}
{{- end }}

{{- define "cryo.migrationImagePullPolicy" -}}
{{- $m := .Values.migration.image | default dict -}}
{{- default .Values.image.pullPolicy $m.pullPolicy -}}
{{- end }}

{{- define "cryo.validateProductionImages" -}}
{{- if eq (default "dev" (index .Values.global.env "CRYO_ENVIRONMENT")) "prod" -}}
  {{- if not .Values.image.digest -}}
    {{- fail "production requires image.digest (sha256:...)" -}}
  {{- end -}}
  {{- if and .Values.migration.image.repository (not .Values.migration.image.digest) -}}
    {{- fail "production with a separate migration.image.repository requires migration.image.digest (sha256:...)" -}}
  {{- end -}}
  {{- if and .Values.native.enabled (not .Values.native.image.digest) -}}
    {{- fail "production with native.enabled requires native.image.digest (sha256:...)" -}}
  {{- end -}}
  {{- if and .Values.spark.k8s.enabled (not .Values.spark.k8s.image.digest) -}}
    {{- fail "production with spark.k8s.enabled requires spark.k8s.image.digest (sha256:...)" -}}
  {{- end -}}
  {{- if and .Values.web.enabled (not .Values.web.image.digest) -}}
    {{- fail "production with web.enabled requires web.image.digest (sha256:...)" -}}
  {{- end -}}
  {{- if and .Values.redis.enabled (not .Values.redis.image.digest) -}}
    {{- fail "production with redis.enabled requires redis.image.digest (sha256:...)" -}}
  {{- end -}}
{{- end -}}
{{- end }}

{{/*
Name of the ServiceAccount Cryo's pods run as. Defaults to the release fullname so a
default install gets a real, annotatable SA; point serviceAccount.name at an existing one
(and set create=false) to reuse an SA managed outside this chart.
*/}}
{{- define "cryo.serviceAccountName" -}}
{{- default (include "cryo.fullname" .) .Values.serviceAccount.name }}
{{- end }}

{{- define "cryo.secretStoreName" -}}
{{- default (printf "%s-secret-store" (include "cryo.fullname" .)) .Values.externalSecret.secretStore.name }}
{{- end }}

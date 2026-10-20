{{/* The service name is the Helm release name, e.g. "payments". */}}
{{- define "svc.name" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Labels used to find this service's pods. These must never change after install. */}}
{{- define "svc.selectorLabels" -}}
app.kubernetes.io/name: {{ include "svc.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* All labels: selector labels plus descriptive ones. */}}
{{- define "svc.labels" -}}
{{ include "svc.selectorLabels" . }}
app.kubernetes.io/part-of: sentinel
app.kubernetes.io/version: {{ .Values.image.tag | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

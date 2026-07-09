{{- define "polyforge.name" -}}
{{ .Chart.Name }}-control-plane
{{- end }}

{{- define "polyforge.labels" -}}
app.kubernetes.io/name: {{ include "polyforge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: polyforge
{{- end }}

{{- define "polyforge.selectorLabels" -}}
app.kubernetes.io/name: {{ include "polyforge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

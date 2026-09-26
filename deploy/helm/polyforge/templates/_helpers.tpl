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

{{/* An image reference: repository@digest when a digest is set (the digest
release.yml signs), else repository:tag with the tag defaulting to the chart's
appVersion. Pass (dict "image" <values.image> "appVersion" .Chart.AppVersion). */}}
{{- define "polyforge.image" -}}
{{- if .image.digest -}}
{{- printf "%s@%s" .image.repository .image.digest -}}
{{- else -}}
{{- printf "%s:%s" .image.repository (default .appVersion .image.tag) -}}
{{- end -}}
{{- end }}

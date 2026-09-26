{{- define "polyforge-operator.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "polyforge-operator.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "polyforge-operator.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "polyforge-operator.labels" -}}
app.kubernetes.io/name: {{ include "polyforge-operator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "polyforge-operator.operatorImage" -}}
{{- if .Values.operator.image.digest -}}
{{- printf "%s@%s" .Values.operator.image.repository .Values.operator.image.digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.operator.image.repository (default .Chart.AppVersion .Values.operator.image.tag) -}}
{{- end -}}
{{- end -}}

{{- define "polyforge-operator.plannerImage" -}}
{{- if .Values.planner.image.digest -}}
{{- printf "%s@%s" .Values.planner.image.repository .Values.planner.image.digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.planner.image.repository (default .Chart.AppVersion .Values.planner.image.tag) -}}
{{- end -}}
{{- end -}}

{{/* Name and key of the Secret holding the planner's shared bearer token —
     either the user's existingSecret or the chart-managed one. */}}
{{- define "polyforge-operator.plannerAuthSecretName" -}}
{{- if .Values.planner.auth.existingSecret -}}
{{- .Values.planner.auth.existingSecret -}}
{{- else -}}
{{- printf "%s-planner-auth" (include "polyforge-operator.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "polyforge-operator.plannerAuthSecretKey" -}}
{{- default "token" .Values.planner.auth.existingSecretKey -}}
{{- end -}}

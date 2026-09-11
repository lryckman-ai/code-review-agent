{{- define "codereview.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "codereview.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "codereview.labels" -}}
app.kubernetes.io/name: {{ include "codereview.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "codereview.configName" -}}{{ include "codereview.fullname" . }}-config{{- end -}}
{{- define "codereview.secretName" -}}{{ include "codereview.fullname" . }}-secret{{- end -}}
{{- define "codereview.apiService" -}}{{ include "codereview.fullname" . }}-api{{- end -}}
{{- define "codereview.dashboardService" -}}{{ include "codereview.fullname" . }}-dashboard{{- end -}}

{{/*
Env shared by every backend pod (3 reviewers + api): LLM config from the
ConfigMap, credentials from the Secret.
*/}}
{{- define "codereview.backendEnvFrom" -}}
- configMapRef:
    name: {{ include "codereview.configName" . }}
- secretRef:
    name: {{ include "codereview.secretName" . }}
{{- end -}}

# Traces: a single Tempo pod receives OTLP from the services.
# Grafana gets Tempo as a datasource through monitoring.yaml (additionalDataSources).
resource "helm_release" "tempo" {
  name             = "tempo"
  repository       = "https://grafana-community.github.io/helm-charts"
  chart            = "tempo"
  version          = "3.1.0"
  namespace        = "tracing"
  create_namespace = true
  values           = [file("${local.values_dir}/tempo.yaml")]
  timeout          = 300
}

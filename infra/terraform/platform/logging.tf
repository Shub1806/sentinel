# Logs: Loki stores them, Alloy collects them from every pod in the sentinel namespace.
# Grafana gets Loki as a datasource through monitoring.yaml (additionalDataSources).
resource "helm_release" "loki" {
  name             = "loki"
  repository       = "https://grafana-community.github.io/helm-charts"
  chart            = "loki"
  version          = "18.15.1"
  namespace        = "logging"
  create_namespace = true
  values           = [file("${local.values_dir}/loki.yaml")]
  timeout          = 600
}

resource "helm_release" "alloy" {
  name       = "alloy"
  repository = "https://grafana.github.io/helm-charts"
  chart      = "alloy"
  version    = "1.13.1"
  namespace  = "logging"
  values     = [file("${local.values_dir}/alloy.yaml")]
  timeout    = 300

  depends_on = [helm_release.loki] # the "logging" namespace and the Loki endpoint
}

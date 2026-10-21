# Prometheus, Grafana, Alertmanager and Kubernetes exporters.
# Independent of the other releases, so Terraform installs it in parallel with them.
resource "helm_release" "monitoring" {
  name             = "monitoring"
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "kube-prometheus-stack"
  version          = "92.10.1"
  namespace        = "monitoring"
  create_namespace = true
  values           = [file("${local.values_dir}/monitoring.yaml")]
  timeout          = 600
}

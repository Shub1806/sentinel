# The on-call agent. Same chart as the other services, but its own namespace.
resource "helm_release" "agent" {
  name             = "agent"
  chart            = "${local.charts_dir}/sentinel-service"
  namespace        = "agent"
  create_namespace = true
  values           = [file("${local.values_dir}/agent.yaml")]
  timeout          = 300

  set = [
    {
      name  = "image.tag"
      value = lookup(var.image_tags, "agent", "dev")
    }
  ]

  depends_on = [helm_release.monitoring] # the ServiceMonitor type must exist first
}

# The agent's Kubernetes identity: a ServiceAccount with read-only access to "sentinel".
resource "helm_release" "agent_access" {
  name             = "agent-access"
  chart            = "${local.charts_dir}/agent-access"
  namespace        = "agent"
  create_namespace = true

  depends_on = [helm_release.infra] # the "sentinel" namespace must exist first
}

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

  depends_on = [helm_release.monitoring, helm_release.agent_access] # ServiceMonitor type, ServiceAccount
}

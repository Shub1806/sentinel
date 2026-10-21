# Stack 2: everything inside the cluster.
# Order (Terraform works it out from depends_on):
#   CNPG operator -> sentinel-infra (Redpanda, Postgres, LocalStack secret) -> LocalStack -> 3 apps

locals {
  namespace  = "sentinel"
  charts_dir = abspath("${path.module}/../../charts")
  values_dir = abspath("${path.module}/../../helm-values")
  services   = toset(["orders", "payments", "notifications"])
}

# The CloudNativePG operator (adds the Postgres "Cluster" object type to Kubernetes).
resource "helm_release" "cnpg" {
  name             = "cnpg"
  repository       = "https://cloudnative-pg.github.io/charts"
  chart            = "cloudnative-pg"
  version          = "0.29.0"
  namespace        = "cnpg-system"
  create_namespace = true
  wait             = true
}

# Redpanda + topics Job, the Postgres cluster, and the LocalStack token Secret.
resource "helm_release" "infra" {
  name             = "sentinel-infra"
  chart            = "${local.charts_dir}/sentinel-infra"
  namespace        = local.namespace
  create_namespace = true
  wait             = true
  wait_for_jobs    = true # don't continue until the topics exist
  timeout          = 600

  set_sensitive = [
    {
      name  = "localstackAuthToken"
      value = var.localstack_auth_token
    }
  ]

  depends_on = [helm_release.cnpg] # the Postgres "Cluster" type must exist first
}

resource "helm_release" "localstack" {
  name       = "localstack"
  repository = "https://localstack.github.io/helm-charts"
  chart      = "localstack"
  version    = "0.7.1"
  namespace  = local.namespace
  values     = [file("${local.values_dir}/localstack.yaml")]
  timeout    = 600 # first start downloads a ~1 GB image

  depends_on = [helm_release.infra] # needs the localstack-auth Secret
}

# One release per service, all from the shared chart.
resource "helm_release" "apps" {
  for_each = local.services

  name      = each.key
  chart     = "${local.charts_dir}/sentinel-service"
  namespace = local.namespace
  values    = [file("${local.values_dir}/${each.key}.yaml")]
  timeout   = 300

  set = [
    {
      name  = "image.tag"
      value = lookup(var.image_tags, each.key, "dev")
    }
  ]


    depends_on = [helm_release.infra, helm_release.localstack, helm_release.monitoring]
}

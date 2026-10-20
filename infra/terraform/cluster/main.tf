# Stack 1: the k3d cluster itself.
# Run before the "platform" stack, which needs a cluster to talk to.

terraform {
  required_version = ">= 1.16"
}

locals {
  cluster_name = "sentinel"
  config_file  = abspath("${path.module}/../../k3d/cluster.yaml")
}

# k3d has no official Terraform provider, so Terraform runs the k3d command for us
# and records that the cluster exists, which is how "terraform destroy" knows to delete it.
resource "terraform_data" "cluster" {
  input = {
    name        = local.cluster_name
    config_file = local.config_file
  }

  # If the cluster's config file changes, re-create the cluster.
  triggers_replace = [filesha256(local.config_file)]

  provisioner "local-exec" {
    command = "k3d cluster create --config '${self.input.config_file}'"
  }

  provisioner "local-exec" {
    when    = destroy
    command = "k3d cluster delete '${self.input.name}'"
  }
}

output "kube_context" {
  description = "kubectl context for this cluster"
  value       = "k3d-${local.cluster_name}"
}

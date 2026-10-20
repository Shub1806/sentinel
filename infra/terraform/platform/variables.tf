variable "kubeconfig_path" {
  description = "kubectl config file"
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "Cluster to deploy into (created by the cluster stack)"
  type        = string
  default     = "k3d-sentinel"
}

variable "localstack_auth_token" {
  description = "LocalStack account token. Set it with the TF_VAR_localstack_auth_token environment variable, never in a file."
  type        = string
  sensitive   = true # Terraform hides it in plan/apply output
}

terraform {
  required_version = ">= 1.16"

  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.3" # any 3.x from 3.3 up, never 4.0 (which could break things)
    }
  }
}

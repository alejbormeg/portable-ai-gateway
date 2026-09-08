# SPDX-License-Identifier: AGPL-3.0-only
terraform {
  required_version = ">= 1.7.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.2"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# infra/terraform/main.tf — provider and Terraform block (SRS v1.0 §6, Cloud requirements).
#
# AWS is the active cloud provider. The azurerm provider block below is kept
# commented out, not deleted, to document the portability NFR (SRS v1.0 §5:
# "containerized design shall allow deployment across multiple cloud
# providers (AWS, Azure) with minimal configuration changes") without
# requiring Azure credentials to `terraform validate` here.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
    # azurerm = {
    #   source  = "hashicorp/azurerm"
    #   version = "~> 3.0"
    # }
  }
}

provider "aws" {
  region = var.aws_region
}

# provider "azurerm" {
#   features {}
#   subscription_id = var.azure_subscription_id
# }

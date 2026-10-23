# infra/terraform/variables.tf

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "ap-south-1"
}

# variable "azure_subscription_id" {
#   description = "Azure subscription id (portability path; not used while AWS is active)."
#   type        = string
#   default     = ""
# }

variable "environment" {
  description = "Deployment environment name (dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "cluster_name" {
  description = "Name of the EKS cluster."
  type        = string
  default     = "ai-event-processor"
}

variable "kubernetes_version" {
  description = "Kubernetes version for the EKS control plane."
  type        = string
  default     = "1.29"
}

variable "vpc_id" {
  description = "VPC to deploy the cluster into. Placeholder: supply a real VPC id before apply."
  type        = string
  default     = "vpc-00000000000000000"
}

variable "subnet_ids" {
  description = "Subnet ids for the EKS cluster and node group (>= 2 AZs). Placeholder values."
  type        = list(string)
  default     = ["subnet-00000000000000001", "subnet-00000000000000002"]
}

# ── Node group sizing (REQ-041/REQ-045: scale with workload, placeholder sizing) ──
variable "node_instance_type" {
  description = "EC2 instance type for the managed node group."
  type        = string
  default     = "t3.large"
}

variable "node_group_min_size" {
  description = "Minimum node count."
  type        = number
  default     = 2
}

variable "node_group_max_size" {
  description = "Maximum node count."
  type        = number
  default     = 6
}

variable "node_group_desired_size" {
  description = "Desired node count at creation."
  type        = number
  default     = 2
}

variable "k8s_namespace" {
  description = "Kubernetes namespace the services run in (infra/k8s/namespace.yaml)."
  type        = string
  default     = "event-processor"
}

# ── S3 event archive (REQ-028 to REQ-030) ──
variable "create_archive_bucket" {
  description = "Create the archive bucket. Set false to use an existing bucket (archive_bucket_name)."
  type        = bool
  default     = true
}

variable "archive_bucket_name" {
  description = "Archive bucket name. Empty = <cluster_name>-event-archive-<account id>."
  type        = string
  default     = ""
}

variable "archive_prefix" {
  description = "Key prefix the archiver writes under (S3_ARCHIVE_PREFIX)."
  type        = string
  default     = "events"
}

variable "archive_transition_days" {
  description = "Days before archived events move to Glacier Instant Retrieval (REQ-029)."
  type        = number
  default     = 30
}

variable "archive_retention_days" {
  description = "Days archived events are kept before deletion (REQ-029)."
  type        = number
  default     = 365

  validation {
    condition     = var.archive_retention_days > var.archive_transition_days
    error_message = "archive_retention_days must be greater than archive_transition_days."
  }
}

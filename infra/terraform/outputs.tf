# infra/terraform/outputs.tf

output "cluster_name" {
  description = "Name of the EKS cluster."
  value       = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  description = "EKS control plane endpoint, for configuring kubectl/kustomize access."
  value       = aws_eks_cluster.this.endpoint
}

output "cluster_certificate_authority_data" {
  description = "Base64-encoded CA certificate for the cluster."
  value       = aws_eks_cluster.this.certificate_authority[0].data
  sensitive   = true
}

output "node_group_status" {
  description = "Status of the default managed node group."
  value       = aws_eks_node_group.default.status
}

output "archive_bucket_name" {
  description = "S3 bucket the event archiver writes to (S3_ARCHIVE_BUCKET)."
  value       = local.archive_bucket_name
}

output "archiver_role_arn" {
  description = "IAM role for the event-archiver ServiceAccount (eks.amazonaws.com/role-arn annotation)."
  value       = aws_iam_role.archiver.arn
}

output "ecr_registry" {
  description = "ECR registry host for the Jenkins REGISTRY variable."
  value       = split("/", values(aws_ecr_repository.service)[0].repository_url)[0]
}

output "ecr_repositories" {
  description = "ECR repository URL per service."
  value       = { for k, r in aws_ecr_repository.service : k => r.repository_url }
}

# infra/terraform/ecr.tf — container registries the Jenkins "Push" stage
# publishes to (REGISTRY = terraform output `ecr_registry`).

locals {
  services = ["ingestion-service", "forecasting-service", "diagnostic-service", "event-archiver"]
}

resource "aws_ecr_repository" "service" {
  for_each             = toset(local.services)
  name                 = "${var.cluster_name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Environment = var.environment
    Project     = "ai-driven-event-processor"
  }
}

# Keep the last 30 images per service.
resource "aws_ecr_lifecycle_policy" "service" {
  for_each   = aws_ecr_repository.service
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 30 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}

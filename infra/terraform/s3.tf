# infra/terraform/s3.tf — event archive bucket (REQ-028 to REQ-030) and the
# IAM role the in-cluster archiver assumes to write to it (IRSA).
#
# Bring-your-own bucket: if you already created the bucket by hand, set
#   create_archive_bucket = false
#   archive_bucket_name   = "<your-bucket>"
# and Terraform only creates the archiver's IAM role/policy against it.

locals {
  archive_bucket_name = var.archive_bucket_name != "" ? var.archive_bucket_name : "${var.cluster_name}-event-archive-${data.aws_caller_identity.current.account_id}"
  archive_bucket_arn  = var.create_archive_bucket ? aws_s3_bucket.archive[0].arn : data.aws_s3_bucket.existing_archive[0].arn
}

data "aws_caller_identity" "current" {}

data "aws_s3_bucket" "existing_archive" {
  count  = var.create_archive_bucket ? 0 : 1
  bucket = local.archive_bucket_name
}

resource "aws_s3_bucket" "archive" {
  count  = var.create_archive_bucket ? 1 : 0
  bucket = local.archive_bucket_name

  tags = {
    Environment = var.environment
    Project     = "ai-driven-event-processor"
    Purpose     = "event-archive"
  }
}

resource "aws_s3_bucket_public_access_block" "archive" {
  count                   = var.create_archive_bucket ? 1 : 0
  bucket                  = aws_s3_bucket.archive[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "archive" {
  count  = var.create_archive_bucket ? 1 : 0
  bucket = aws_s3_bucket.archive[0].id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Versioning: an overwritten batch (deterministic keys, REQ-030) keeps its
# previous version until the lifecycle rule below expires it.
resource "aws_s3_bucket_versioning" "archive" {
  count  = var.create_archive_bucket ? 1 : 0
  bucket = aws_s3_bucket.archive[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "archive" {
  count  = var.create_archive_bucket ? 1 : 0
  bucket = aws_s3_bucket.archive[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# REQ-029: retention policy — cold storage after N days, delete after M days.
resource "aws_s3_bucket_lifecycle_configuration" "archive" {
  count  = var.create_archive_bucket ? 1 : 0
  bucket = aws_s3_bucket.archive[0].id

  rule {
    id     = "event-retention"
    status = "Enabled"
    filter {
      prefix = "events/"
    }
    transition {
      days          = var.archive_transition_days
      storage_class = "GLACIER_IR"
    }
    expiration {
      days = var.archive_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# REQ-047: refuse any non-TLS access to the archive.
resource "aws_s3_bucket_policy" "archive_tls_only" {
  count  = var.create_archive_bucket ? 1 : 0
  bucket = aws_s3_bucket.archive[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.archive[0].arn, "${aws_s3_bucket.archive[0].arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.archive]
}

# ── IRSA: the archiver pod's IAM role ────────────────────────────────────────

data "tls_certificate" "eks_oidc" {
  url = aws_eks_cluster.this.identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "eks" {
  url             = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks_oidc.certificates[0].sha1_fingerprint]
}

locals {
  oidc_issuer = replace(aws_iam_openid_connect_provider.eks.url, "https://", "")
}

resource "aws_iam_role" "archiver" {
  name = "${var.cluster_name}-archiver"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.eks.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_issuer}:sub" = "system:serviceaccount:${var.k8s_namespace}:event-archiver"
          "${local.oidc_issuer}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
}

# Least privilege: write objects under the archive prefix, nothing else.
resource "aws_iam_role_policy" "archiver_s3" {
  name = "archive-write"
  role = aws_iam_role.archiver.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:AbortMultipartUpload"]
      Resource = "${local.archive_bucket_arn}/${var.archive_prefix}/*"
    }]
  })
}

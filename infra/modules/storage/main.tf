locals {
  firehose_name = "${var.name_prefix}-raw"

  # Firehose evaluates !{...} at delivery time, using the record's approximate
  # arrival time in UTC. Hive-style keys let Athena project the partitions.
  raw_prefix   = "raw/dt=!{timestamp:yyyy-MM-dd}/hour=!{timestamp:HH}/"
  error_prefix = "errors/!{firehose:error-output-type}/dt=!{timestamp:yyyy-MM-dd}/"
}

data "aws_caller_identity" "current" {}

# ---- Lake bucket -----------------------------------------------------------

# Bucket names are global, so the account ID keeps this one from colliding.
resource "aws_s3_bucket" "lake" {
  bucket        = "${var.name_prefix}-lake-${data.aws_caller_identity.current.account_id}"
  force_destroy = var.force_destroy
  tags          = var.tags
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "lake_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "lake" {
  bucket = aws_s3_bucket.lake.id
  policy = data.aws_iam_policy_document.lake_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.lake]
}

# Raw JSON is a landing zone, not the system of record: phase 3 rewrites it
# as Parquet under processed/, so raw/ only needs to cover reprocessing.
resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    id     = "expire-raw"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    expiration {
      days = var.raw_expiration_days
    }
  }

  rule {
    id     = "expire-errors"
    status = "Enabled"

    filter {
      prefix = "errors/"
    }

    expiration {
      days = var.raw_expiration_days
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ---- Firehose: stream -> raw/ ----------------------------------------------

resource "aws_cloudwatch_log_group" "firehose" {
  name              = "/aws/kinesisfirehose/${local.firehose_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_cloudwatch_log_stream" "firehose_delivery" {
  name           = "DestinationDelivery"
  log_group_name = aws_cloudwatch_log_group.firehose.name
}

data "aws_iam_policy_document" "firehose_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }

    # Firehose passes the account ID as the external ID; this stops another
    # account's delivery stream from assuming the role (confused deputy).
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "firehose" {
  name               = "${local.firehose_name}-firehose"
  assume_role_policy = data.aws_iam_policy_document.firehose_assume.json
  tags               = var.tags
}

# Least privilege: read this one stream, write only under raw/ and errors/ in
# the lake bucket, and log to its own log stream. Nothing else. The stream is
# encrypted with the AWS-managed aws/kinesis key, whose key policy already
# allows account principals to use it through Kinesis, so no KMS grant here.
data "aws_iam_policy_document" "firehose" {
  statement {
    sid = "ReadSourceStream"
    actions = [
      "kinesis:DescribeStream",
      "kinesis:GetShardIterator",
      "kinesis:GetRecords",
      "kinesis:ListShards",
    ]
    resources = [var.source_stream_arn]
  }

  statement {
    sid = "LocateLakeBucket"
    actions = [
      "s3:GetBucketLocation",
      "s3:ListBucket",
      "s3:ListBucketMultipartUploads",
    ]
    resources = [aws_s3_bucket.lake.arn]
  }

  statement {
    sid = "WriteRawObjects"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = [
      "${aws_s3_bucket.lake.arn}/raw/*",
      "${aws_s3_bucket.lake.arn}/errors/*",
    ]
  }

  statement {
    sid       = "WriteDeliveryLogs"
    actions   = ["logs:PutLogEvents"]
    resources = [aws_cloudwatch_log_stream.firehose_delivery.arn]
  }
}

resource "aws_iam_role_policy" "firehose" {
  name   = "${local.firehose_name}-firehose-policy"
  role   = aws_iam_role.firehose.id
  policy = data.aws_iam_policy_document.firehose.json
}

resource "aws_kinesis_firehose_delivery_stream" "raw" {
  name        = local.firehose_name
  destination = "extended_s3"

  kinesis_source_configuration {
    kinesis_stream_arn = var.source_stream_arn
    role_arn           = aws_iam_role.firehose.arn
  }

  extended_s3_configuration {
    role_arn            = aws_iam_role.firehose.arn
    bucket_arn          = aws_s3_bucket.lake.arn
    prefix              = local.raw_prefix
    error_output_prefix = local.error_prefix
    buffering_interval  = var.buffering_interval_s
    buffering_size      = var.buffering_size_mb
    compression_format  = var.compression_format

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose.name
      log_stream_name = aws_cloudwatch_log_stream.firehose_delivery.name
    }
  }

  tags = var.tags

  # Firehose checks the role's permissions on create, so the policy must be
  # attached first.
  depends_on = [aws_iam_role_policy.firehose]
}

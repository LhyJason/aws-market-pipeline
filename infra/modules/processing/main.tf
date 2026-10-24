locals {
  function_name = "${var.name_prefix}-processor"
  stream_name   = element(split("/", var.stream_arn), 1)
}

# ---- Processor Lambda ------------------------------------------------------

# The package directory is built by scripts/build_lambda.py (it bundles the
# DuckDB wheel for var.architecture); Terraform only zips it.
data "archive_file" "processor" {
  type        = "zip"
  source_dir  = var.package_dir
  output_path = "${path.module}/.build/processor-${var.architecture}.zip"
}

resource "aws_cloudwatch_log_group" "processor" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "processor" {
  name               = local.function_name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

# Least privilege: read this one stream, read and write this one table, write
# objects under processed/ only, send failure records to its own DLQ, log
# to its own log group, and send traces. The stream uses the AWS-managed aws/kinesis key, whose
# key policy already lets account principals decrypt through Kinesis.
data "aws_iam_policy_document" "processor" {
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.processor.arn}:*"]
  }

  statement {
    sid = "ReadStream"
    actions = [
      "kinesis:DescribeStream",
      "kinesis:DescribeStreamSummary",
      "kinesis:GetRecords",
      "kinesis:GetShardIterator",
      "kinesis:ListShards",
    ]
    resources = [var.stream_arn]
  }

  statement {
    sid = "ReadWriteMarketTable"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
    ]
    resources = [var.table_arn]
  }

  statement {
    sid       = "WriteProcessedObjects"
    actions   = ["s3:PutObject"]
    resources = ["${var.lake_bucket_arn}/processed/*"]
  }

  # The event-source mapping sends on-failure records with the function's role.
  statement {
    sid       = "SendToDeadLetterQueue"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.dlq.arn]
  }

  # X-Ray's write actions have no resource-level permissions, so this is one
  # of the few statements on "*".
  statement {
    sid       = "WriteTraces"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "processor" {
  name   = "${local.function_name}-policy"
  role   = aws_iam_role.processor.id
  policy = data.aws_iam_policy_document.processor.json
}

resource "aws_lambda_function" "processor" {
  function_name = local.function_name
  description   = "Builds 1-minute bars from Kinesis ticks into DynamoDB and Parquet"
  role          = aws_iam_role.processor.arn
  runtime       = "python3.12"
  architectures = [var.architecture]
  handler       = "processor.handler.lambda_handler"
  timeout       = var.timeout_s
  memory_size   = var.memory_size

  filename         = data.archive_file.processor.output_path
  source_code_hash = data.archive_file.processor.output_base64sha256

  # Active: Lambda samples invocations and sends segments to X-Ray.
  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      TABLE_NAME       = var.table_name
      LAKE_BUCKET      = var.lake_bucket
      PROCESSED_PREFIX = var.processed_prefix
      BAR_TTL_DAYS     = tostring(var.bar_ttl_days)
      LOG_LEVEL        = var.log_level
    }
  }

  tags = var.tags

  depends_on = [
    aws_cloudwatch_log_group.processor,
    aws_iam_role_policy.processor,
  ]
}

# ---- Stream -> Lambda ------------------------------------------------------

# Kinesis on-failure destinations receive metadata about the failed batch
# (shard, sequence range), not the records; replay them from the stream or
# raw/. Retention covers the stream's 24 hours with room to spare.
resource "aws_sqs_queue" "dlq" {
  name                      = "${local.function_name}-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}

resource "aws_lambda_event_source_mapping" "stream" {
  event_source_arn  = var.stream_arn
  function_name     = aws_lambda_function.processor.arn
  starting_position = "TRIM_HORIZON" # writes are idempotent, so reading the backlog is safe

  batch_size                         = var.batch_size
  maximum_batching_window_in_seconds = var.batching_window_s
  parallelization_factor             = 1 # one batch per shard at a time; the store relies on it

  # The handler reports the earliest failed record; Lambda retries from there.
  function_response_types        = ["ReportBatchItemFailures"]
  bisect_batch_on_function_error = true
  maximum_retry_attempts         = var.maximum_retry_attempts
  maximum_record_age_in_seconds  = var.maximum_record_age_s

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.dlq.arn
    }
  }

  depends_on = [aws_iam_role_policy.processor]
}

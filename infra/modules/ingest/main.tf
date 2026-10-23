locals {
  function_name = "${var.name_prefix}-producer"
}

# ---- Stream ----------------------------------------------------------------

resource "aws_kinesis_stream" "ticks" {
  name             = "${var.name_prefix}-ticks"
  shard_count      = var.shard_count
  retention_period = var.retention_hours
  encryption_type  = "KMS"
  kms_key_id       = "alias/aws/kinesis"

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  tags = var.tags
}

# ---- Producer Lambda -------------------------------------------------------

data "archive_file" "producer" {
  type        = "zip"
  source_dir  = var.producer_source_dir
  output_path = "${path.module}/.build/producer.zip"
}

resource "aws_cloudwatch_log_group" "producer" {
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

resource "aws_iam_role" "producer" {
  name               = local.function_name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

# Least privilege: this role can write to its own log group and put records
# on this one stream. Nothing else.
data "aws_iam_policy_document" "producer" {
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.producer.arn}:*"]
  }

  statement {
    sid       = "PutToStream"
    actions   = ["kinesis:PutRecord", "kinesis:PutRecords"]
    resources = [aws_kinesis_stream.ticks.arn]
  }
}

resource "aws_iam_role_policy" "producer" {
  name   = "${local.function_name}-policy"
  role   = aws_iam_role.producer.id
  policy = data.aws_iam_policy_document.producer.json
}

resource "aws_lambda_function" "producer" {
  function_name = local.function_name
  description   = "Fetches spot prices and publishes them to Kinesis"
  role          = aws_iam_role.producer.arn
  runtime       = "python3.12"
  architectures = ["arm64"]
  handler       = "producer.handler.lambda_handler"
  timeout       = 30
  memory_size   = 256

  filename         = data.archive_file.producer.output_path
  source_code_hash = data.archive_file.producer.output_base64sha256

  environment {
    variables = {
      KINESIS_STREAM_NAME = aws_kinesis_stream.ticks.name
      SYMBOLS             = join(",", var.symbols)
      FEED_BASE_URL       = var.feed_base_url
      LOG_LEVEL           = var.log_level
    }
  }

  tags = var.tags

  depends_on = [
    aws_cloudwatch_log_group.producer,
    aws_iam_role_policy.producer,
  ]
}

# ---- Schedule --------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "${local.function_name}-schedule"
  description         = "Invokes the producer on a fixed schedule"
  schedule_expression = var.schedule_expression
  state               = var.enabled ? "ENABLED" : "DISABLED"
  tags                = var.tags
}

resource "aws_cloudwatch_event_target" "producer" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = "producer"
  arn       = aws_lambda_function.producer.arn
}

resource "aws_lambda_permission" "allow_events" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.producer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}

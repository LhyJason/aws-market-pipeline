data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  alarms = concat(
    aws_cloudwatch_metric_alarm.producer_errors,
    aws_cloudwatch_metric_alarm.iterator_age,
    aws_cloudwatch_metric_alarm.dlq_depth,
    aws_cloudwatch_metric_alarm.api_5xx,
    aws_cloudwatch_metric_alarm.api_latency,
  )
}

# ---- Notification topic ----------------------------------------------------

# No SSE: CloudWatch cannot publish to a topic encrypted with the AWS-managed
# aws/sns key, a customer-managed key costs $1 a month, and alarm messages
# carry nothing secret.
resource "aws_sns_topic" "alarms" {
  name = "${var.name_prefix}-alarms"
  tags = var.tags
}

# Only this account's <name_prefix>-* alarms may publish, and only over TLS.
# Account principals still manage the topic through their IAM policies.
data "aws_iam_policy_document" "alarms_topic" {
  statement {
    sid       = "CloudWatchAlarmsPublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${data.aws_partition.current.partition}:cloudwatch:${local.region}:${local.account_id}:alarm:${var.name_prefix}-*"]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms.arn]

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

resource "aws_sns_topic_policy" "alarms" {
  arn    = aws_sns_topic.alarms.arn
  policy = data.aws_iam_policy_document.alarms_topic.json
}

# Stays "pending confirmation" until the link in AWS's email is clicked.
resource "aws_sns_topic_subscription" "email" {
  count = var.alarm_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ---- Alarms ------------------------------------------------------------------
# Every alarm notifies on ALARM and on OK, and treats missing data as fine:
# a quiet API or an empty queue is not an incident.

resource "aws_cloudwatch_metric_alarm" "producer_errors" {
  count = var.create_alarms ? 1 : 0

  alarm_name          = "${var.name_prefix}-producer-errors"
  alarm_description   = "The producer failed in ${var.producer_error_minutes} of the last 5 minutes: the feed is down or ticks are not reaching ${var.stream_name}"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = var.producer_function_name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = var.producer_error_minutes
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "iterator_age" {
  count = var.create_alarms ? 1 : 0

  alarm_name          = "${var.name_prefix}-processor-iterator-age"
  alarm_description   = "The processor is falling behind ${var.stream_name}"
  namespace           = "AWS/Lambda"
  metric_name         = "IteratorAge"
  dimensions          = { FunctionName = var.processor_function_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = var.iterator_age_alarm_ms
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

# Stays in ALARM until the queue is drained, so a failed batch cannot be
# forgotten. SQS stops reporting an idle queue after a few hours and can take
# up to 15 minutes to report again.
resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  count = var.create_alarms ? 1 : 0

  alarm_name          = "${var.name_prefix}-processor-dlq"
  alarm_description   = "A processor batch failed every retry; replay its sequence range from the stream or raw/"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = var.dlq_name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

# Stage-level metrics are free; they do not need detailed (per-method) metrics.
resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  count = var.create_alarms ? 1 : 0

  alarm_name          = "${var.name_prefix}-api-5xx"
  alarm_description   = "The API returned ${var.api_5xx_threshold} or more 5xx responses in 5 minutes (503 means DynamoDB errors, 502 a broken Lambda)"
  namespace           = "AWS/ApiGateway"
  metric_name         = "5XXError"
  dimensions          = { ApiName = var.api_name, Stage = var.api_stage }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.api_5xx_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

# Three 5-minute periods in a row, so a single cold start does not page.
resource "aws_cloudwatch_metric_alarm" "api_latency" {
  count = var.create_alarms ? 1 : 0

  alarm_name          = "${var.name_prefix}-api-latency"
  alarm_description   = "The API's p95 latency stayed above ${var.api_latency_p95_ms} ms for 15 minutes"
  namespace           = "AWS/ApiGateway"
  metric_name         = "Latency"
  dimensions          = { ApiName = var.api_name, Stage = var.api_stage }
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 3
  threshold           = var.api_latency_p95_ms
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = var.tags
}

# ---- Dashboard ---------------------------------------------------------------

locals {
  api_dims = ["ApiName", var.api_name, "Stage", var.api_stage]

  # Widgets without x/y are laid out left to right, top to bottom.
  graph = {
    type   = "metric"
    width  = 12
    height = 6
  }

  widgets = concat(
    var.create_alarms ? [{
      type   = "alarm"
      width  = 24
      height = 2
      properties = {
        title  = "Alarms"
        alarms = [for alarm in local.alarms : alarm.arn]
      }
    }] : [],
    [
      merge(local.graph, { properties = {
        title  = "Producer"
        region = local.region
        view   = "timeSeries"
        period = 60
        stat   = "Sum"
        metrics = [
          ["AWS/Lambda", "Invocations", "FunctionName", var.producer_function_name],
          ["AWS/Lambda", "Errors", "FunctionName", var.producer_function_name],
        ]
      } }),
      merge(local.graph, { properties = {
        title  = "Stream and raw/"
        region = local.region
        view   = "timeSeries"
        period = 60
        stat   = "Sum"
        metrics = [
          ["AWS/Kinesis", "IncomingRecords", "StreamName", var.stream_name],
          ["AWS/Firehose", "DeliveryToS3.Records", "DeliveryStreamName", var.firehose_name],
        ]
      } }),
      merge(local.graph, { properties = {
        title  = "Processor"
        region = local.region
        view   = "timeSeries"
        period = 60
        metrics = [
          ["AWS/Lambda", "IteratorAge", "FunctionName", var.processor_function_name, { stat = "Maximum", label = "iterator age max (ms)" }],
          ["AWS/Lambda", "Duration", "FunctionName", var.processor_function_name, { stat = "p95", label = "duration p95 (ms)" }],
          ["AWS/Lambda", "Errors", "FunctionName", var.processor_function_name, { stat = "Sum", yAxis = "right" }],
        ]
      } }),
      merge(local.graph, { properties = {
        title  = "End-to-end lag: tick observed to bar written (ms)"
        region = local.region
        view   = "timeSeries"
        period = 60
        metrics = [
          ["MarketPipeline", "TickLagMs", "FunctionName", var.processor_function_name, { stat = "p50", label = "p50" }],
          ["MarketPipeline", "TickLagMs", "FunctionName", var.processor_function_name, { stat = "p95", label = "p95" }],
          ["MarketPipeline", "TickLagMs", "FunctionName", var.processor_function_name, { stat = "Maximum", label = "max" }],
        ]
      } }),
      merge(local.graph, { properties = {
        title  = "Dead-letter queue and DynamoDB"
        region = local.region
        view   = "timeSeries"
        period = 300
        metrics = [
          ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.dlq_name, { stat = "Maximum", label = "DLQ messages" }],
          ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", var.table_name, { stat = "Sum", yAxis = "right" }],
          ["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", var.table_name, { stat = "Sum", yAxis = "right" }],
          ["AWS/DynamoDB", "WriteThrottleEvents", "TableName", var.table_name, { stat = "Sum" }],
          ["AWS/DynamoDB", "ReadThrottleEvents", "TableName", var.table_name, { stat = "Sum" }],
        ]
      } }),
      merge(local.graph, { properties = {
        title  = "API requests"
        region = local.region
        view   = "timeSeries"
        period = 300
        stat   = "Sum"
        metrics = [
          concat(["AWS/ApiGateway", "Count"], local.api_dims),
          concat(["AWS/ApiGateway", "4XXError"], local.api_dims),
          concat(["AWS/ApiGateway", "5XXError"], local.api_dims),
        ]
      } }),
      merge(local.graph, { properties = {
        title  = "API latency (ms)"
        region = local.region
        view   = "timeSeries"
        period = 300
        metrics = [
          concat(["AWS/ApiGateway", "Latency"], local.api_dims, [{ stat = "p50", label = "latency p50" }]),
          concat(["AWS/ApiGateway", "Latency"], local.api_dims, [{ stat = "p95", label = "latency p95" }]),
          concat(["AWS/ApiGateway", "IntegrationLatency"], local.api_dims, [{ stat = "p95", label = "Lambda p95" }]),
        ]
      } }),
      merge(local.graph, { properties = {
        title  = "API Lambda"
        region = local.region
        view   = "timeSeries"
        period = 300
        metrics = [
          ["AWS/Lambda", "Duration", "FunctionName", var.api_function_name, { stat = "p95", label = "duration p95 (ms)" }],
          ["AWS/Lambda", "Errors", "FunctionName", var.api_function_name, { stat = "Sum", yAxis = "right" }],
          ["AWS/Lambda", "Throttles", "FunctionName", var.api_function_name, { stat = "Sum", yAxis = "right" }],
        ]
      } }),
    ],
  )
}

resource "aws_cloudwatch_dashboard" "pipeline" {
  dashboard_name = var.name_prefix
  dashboard_body = jsonencode({ widgets = local.widgets })
}

locals {
  function_name = "${var.name_prefix}-api"

  # path part -> whether the route needs the API key. /health stays open for
  # uptime checks; everything that reads the table needs a key.
  routes = {
    prices = true
    bars   = true
  }
}

# ---- API Lambda ------------------------------------------------------------

# The package directory is built by scripts/build_lambda.py (it bundles the
# pydantic-core wheel for var.architecture); Terraform only zips it.
data "archive_file" "api" {
  type        = "zip"
  source_dir  = var.package_dir
  output_path = "${path.module}/.build/api-${var.architecture}.zip"
}

resource "aws_cloudwatch_log_group" "api" {
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

resource "aws_iam_role" "api" {
  name               = local.function_name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

# Least privilege: read-only on the one table (GetItem for LATEST, Query for
# bars; no Scan), and logs to its own log group.
data "aws_iam_policy_document" "api" {
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }

  statement {
    sid       = "ReadMarketTable"
    actions   = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [var.table_arn]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "${local.function_name}-policy"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_lambda_function" "api" {
  function_name = local.function_name
  description   = "FastAPI over the market table: latest prices, bars, rolling stats"
  role          = aws_iam_role.api.arn
  runtime       = "python3.12"
  architectures = [var.architecture]
  handler       = "api.app.handler"
  timeout       = var.timeout_s
  memory_size   = var.memory_size

  filename         = data.archive_file.api.output_path
  source_code_hash = data.archive_file.api.output_base64sha256

  environment {
    variables = {
      TABLE_NAME    = var.table_name
      SYMBOLS       = join(",", var.symbols)
      BAR_TTL_DAYS  = tostring(var.bar_ttl_days)
      ANOMALY_Z     = tostring(var.anomaly_z)
      STALE_AFTER_S = tostring(var.stale_after_s)
      LOG_LEVEL     = var.log_level
    }
  }

  tags = var.tags

  depends_on = [
    aws_cloudwatch_log_group.api,
    aws_iam_role_policy.api,
  ]
}

# ---- REST API --------------------------------------------------------------

# A REST API rather than an HTTP API: it has API keys and usage plans, and it
# runs on LocalStack's free edition, so local tests exercise the same auth.
resource "aws_api_gateway_rest_api" "api" {
  name        = local.function_name
  description = "market-pipeline read API"

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  tags = var.tags
}

# One resource per route instead of a {proxy+}: API Gateway rejects unknown
# paths itself, and each route sets its own key requirement.
resource "aws_api_gateway_resource" "health" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "health"
}

resource "aws_api_gateway_method" "health" {
  rest_api_id      = aws_api_gateway_rest_api.api.id
  resource_id      = aws_api_gateway_resource.health.id
  http_method      = "GET"
  authorization    = "NONE"
  api_key_required = false
}

resource "aws_api_gateway_integration" "health" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.health.id
  http_method             = aws_api_gateway_method.health.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.api.invoke_arn
}

resource "aws_api_gateway_resource" "route" {
  for_each = local.routes

  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = each.key
}

resource "aws_api_gateway_resource" "symbol" {
  for_each = local.routes

  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_resource.route[each.key].id
  path_part   = "{symbol}"
}

resource "aws_api_gateway_method" "symbol" {
  for_each = local.routes

  rest_api_id      = aws_api_gateway_rest_api.api.id
  resource_id      = aws_api_gateway_resource.symbol[each.key].id
  http_method      = "GET"
  authorization    = "NONE"
  api_key_required = each.value

  request_parameters = {
    "method.request.path.symbol" = true
  }
}

resource "aws_api_gateway_integration" "symbol" {
  for_each = local.routes

  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.symbol[each.key].id
  http_method             = aws_api_gateway_method.symbol[each.key].http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.api.invoke_arn
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  # Any stage, GET only, any of this API's routes.
  source_arn = "${aws_api_gateway_rest_api.api.execution_arn}/*/GET/*"
}

# A deployment is a snapshot of the routes; a new one is made whenever they change.
resource "aws_api_gateway_deployment" "api" {
  rest_api_id = aws_api_gateway_rest_api.api.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.health,
      aws_api_gateway_method.health,
      aws_api_gateway_integration.health,
      aws_api_gateway_resource.route,
      aws_api_gateway_resource.symbol,
      aws_api_gateway_method.symbol,
      aws_api_gateway_integration.symbol,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration.health,
    aws_api_gateway_integration.symbol,
  ]
}

# ---- Access logs -----------------------------------------------------------

resource "aws_cloudwatch_log_group" "access" {
  count = var.enable_access_logs ? 1 : 0

  name              = "/aws/apigateway/${local.function_name}/access"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

# API Gateway writes logs with one role per account and region. This is the
# one place an AWS-managed policy is used: the role is shared by every REST
# API in the account, so it cannot be scoped to this API's log group.
data "aws_iam_policy_document" "apigateway_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["apigateway.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apigateway_logs" {
  count = var.manage_account_logging_role ? 1 : 0

  name               = "${var.name_prefix}-apigateway-logs"
  assume_role_policy = data.aws_iam_policy_document.apigateway_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "apigateway_logs" {
  count = var.manage_account_logging_role ? 1 : 0

  role       = aws_iam_role.apigateway_logs[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}

resource "aws_api_gateway_account" "this" {
  count = var.manage_account_logging_role ? 1 : 0

  cloudwatch_role_arn = aws_iam_role.apigateway_logs[0].arn

  depends_on = [aws_iam_role_policy_attachment.apigateway_logs]
}

# ---- Stage, throttling, API key ----------------------------------------------

resource "aws_api_gateway_stage" "api" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  deployment_id = aws_api_gateway_deployment.api.id
  stage_name    = var.stage_name

  dynamic "access_log_settings" {
    for_each = var.enable_access_logs ? [1] : []

    content {
      destination_arn = aws_cloudwatch_log_group.access[0].arn
      format = jsonencode({
        requestId      = "$context.requestId"
        ip             = "$context.identity.sourceIp"
        apiKeyId       = "$context.identity.apiKeyId"
        httpMethod     = "$context.httpMethod"
        resourcePath   = "$context.resourcePath"
        path           = "$context.path"
        status         = "$context.status"
        responseLength = "$context.responseLength"
        latencyMs      = "$context.responseLatency"
        integrationMs  = "$context.integrationLatency"
        error          = "$context.error.message"
      })
    }
  }

  tags = var.tags

  depends_on = [aws_api_gateway_account.this]
}

# Stage-wide default throttle, for every caller, keyed or not.
resource "aws_api_gateway_method_settings" "all" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  stage_name  = aws_api_gateway_stage.api.stage_name
  method_path = "*/*"

  settings {
    throttling_rate_limit  = var.throttle_rate_limit
    throttling_burst_limit = var.throttle_burst_limit
    metrics_enabled        = false
    logging_level          = "OFF"
  }
}

resource "aws_api_gateway_api_key" "default" {
  name    = "${local.function_name}-default"
  enabled = true
  tags    = var.tags
}

resource "aws_api_gateway_usage_plan" "default" {
  name = "${local.function_name}-default"

  api_stages {
    api_id = aws_api_gateway_rest_api.api.id
    stage  = aws_api_gateway_stage.api.stage_name
  }

  throttle_settings {
    rate_limit  = var.throttle_rate_limit
    burst_limit = var.throttle_burst_limit
  }

  quota_settings {
    limit  = var.quota_per_day
    period = "DAY"
  }

  tags = var.tags
}

resource "aws_api_gateway_usage_plan_key" "default" {
  key_id        = aws_api_gateway_api_key.default.id
  key_type      = "API_KEY"
  usage_plan_id = aws_api_gateway_usage_plan.default.id
}

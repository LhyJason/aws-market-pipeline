# ---- GitHub Actions deploy access --------------------------------------------
# GitHub's OIDC tokens are exchanged for short-lived credentials, so no AWS
# keys are stored in GitHub. Three roles:
#   <project>-github-plan          pull requests: read-only, plans every env
#   <project>-github-deploy-<env>  jobs in GitHub environment <env>: apply
# Every action is named (no "service:*"), and resources are scoped to the
# <project>-<env>-* names the modules use. "*" appears only for the few calls
# that take no resource ARN. Roles the deploy role creates must carry the
# environment's permissions boundary, so it cannot mint a role that has more
# power than the workloads need.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  github_oidc_url = "token.actions.githubusercontent.com"

  arn     = "arn:${data.aws_partition.current.partition}"
  region  = var.aws_region
  account = data.aws_caller_identity.current.account_id

  # {env} is replaced by "<project>-<env>" (or "<project>-*" for the plan role)
  # and {db} by the Glue database, which uses underscores.
  services = {
    Kinesis = {
      arns = ["${local.arn}:kinesis:${local.region}:${local.account}:stream/{env}-*"]
      read = [
        "kinesis:DescribeStream",
        "kinesis:DescribeStreamSummary",
        "kinesis:ListShards",
        "kinesis:ListTagsForResource",
        "kinesis:ListTagsForStream",
      ]
      write = [
        "kinesis:AddTagsToStream",
        "kinesis:CreateStream",
        "kinesis:DecreaseStreamRetentionPeriod",
        "kinesis:DeleteStream",
        "kinesis:DisableEnhancedMonitoring",
        "kinesis:EnableEnhancedMonitoring",
        "kinesis:IncreaseStreamRetentionPeriod",
        "kinesis:RemoveTagsFromStream",
        "kinesis:StartStreamEncryption",
        "kinesis:StopStreamEncryption",
        "kinesis:TagResource",
        "kinesis:UntagResource",
        "kinesis:UpdateShardCount",
        "kinesis:UpdateStreamMode",
      ]
    }
    Firehose = {
      arns = ["${local.arn}:firehose:${local.region}:${local.account}:deliverystream/{env}-*"]
      read = ["firehose:DescribeDeliveryStream", "firehose:ListTagsForDeliveryStream"]
      write = [
        "firehose:CreateDeliveryStream",
        "firehose:DeleteDeliveryStream",
        "firehose:StartDeliveryStreamEncryption",
        "firehose:StopDeliveryStreamEncryption",
        "firehose:TagDeliveryStream",
        "firehose:UntagDeliveryStream",
        "firehose:UpdateDestination",
      ]
    }
    LakeBucket = {
      arns = ["${local.arn}:s3:::{env}-lake-*"]
      read = [
        "s3:GetAccelerateConfiguration",
        "s3:GetBucketAcl",
        "s3:GetBucketCORS",
        "s3:GetBucketLocation",
        "s3:GetBucketLogging",
        "s3:GetBucketObjectLockConfiguration",
        "s3:GetBucketOwnershipControls",
        "s3:GetBucketPolicy",
        "s3:GetBucketPublicAccessBlock",
        "s3:GetBucketRequestPayment",
        "s3:GetBucketTagging",
        "s3:GetBucketVersioning",
        "s3:GetBucketWebsite",
        "s3:GetEncryptionConfiguration",
        "s3:GetLifecycleConfiguration",
        "s3:GetReplicationConfiguration",
        "s3:ListBucket",
      ]
      write = [
        "s3:CreateBucket",
        "s3:DeleteBucket",
        "s3:DeleteBucketPolicy",
        "s3:ListBucketVersions",
        "s3:PutBucketPolicy",
        "s3:PutBucketPublicAccessBlock",
        "s3:PutBucketTagging",
        "s3:PutEncryptionConfiguration",
        "s3:PutLifecycleConfiguration",
      ]
    }
    # Only for lake_force_destroy, which empties the bucket before deleting it.
    LakeObjects = {
      arns  = ["${local.arn}:s3:::{env}-lake-*/*"]
      read  = []
      write = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
    }
    DynamoDB = {
      arns = ["${local.arn}:dynamodb:${local.region}:${local.account}:table/{env}-*"]
      read = [
        "dynamodb:DescribeContinuousBackups",
        "dynamodb:DescribeTable",
        "dynamodb:DescribeTimeToLive",
        "dynamodb:ListTagsOfResource",
      ]
      write = [
        "dynamodb:CreateTable",
        "dynamodb:DeleteTable",
        "dynamodb:TagResource",
        "dynamodb:UntagResource",
        "dynamodb:UpdateContinuousBackups",
        "dynamodb:UpdateTable",
        "dynamodb:UpdateTimeToLive",
      ]
    }
    SQS = {
      arns = ["${local.arn}:sqs:${local.region}:${local.account}:{env}-*"]
      read = ["sqs:GetQueueAttributes", "sqs:GetQueueUrl", "sqs:ListQueueTags"]
      write = [
        "sqs:CreateQueue",
        "sqs:DeleteQueue",
        "sqs:SetQueueAttributes",
        "sqs:TagQueue",
        "sqs:UntagQueue",
      ]
    }
    # The topic and its subscriptions (<topic ARN>:<id>).
    SNS = {
      arns = ["${local.arn}:sns:${local.region}:${local.account}:{env}-*"]
      read = [
        "sns:GetSubscriptionAttributes",
        "sns:GetTopicAttributes",
        "sns:ListSubscriptionsByTopic",
        "sns:ListTagsForResource",
      ]
      write = [
        "sns:CreateTopic",
        "sns:DeleteTopic",
        "sns:SetSubscriptionAttributes",
        "sns:SetTopicAttributes",
        "sns:Subscribe",
        "sns:TagResource",
        "sns:Unsubscribe",
        "sns:UntagResource",
      ]
    }
    CloudWatch = {
      arns = [
        "${local.arn}:cloudwatch:${local.region}:${local.account}:alarm:{env}-*",
        "${local.arn}:cloudwatch::${local.account}:dashboard/{env}",
        "${local.arn}:cloudwatch::${local.account}:dashboard/{env}-*",
      ]
      read = ["cloudwatch:GetDashboard", "cloudwatch:ListTagsForResource"]
      write = [
        "cloudwatch:DeleteAlarms",
        "cloudwatch:DeleteDashboards",
        "cloudwatch:PutDashboard",
        "cloudwatch:PutMetricAlarm",
        "cloudwatch:TagResource",
        "cloudwatch:UntagResource",
      ]
    }
    Logs = {
      arns = [
        "${local.arn}:logs:${local.region}:${local.account}:log-group:/aws/lambda/{env}-*",
        "${local.arn}:logs:${local.region}:${local.account}:log-group:/aws/kinesisfirehose/{env}-*",
        "${local.arn}:logs:${local.region}:${local.account}:log-group:/aws/apigateway/{env}-*",
      ]
      read = ["logs:DescribeLogStreams", "logs:ListTagsForResource", "logs:ListTagsLogGroup"]
      write = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:DeleteLogGroup",
        "logs:DeleteLogStream",
        "logs:DeleteRetentionPolicy",
        "logs:PutRetentionPolicy",
        "logs:TagLogGroup",
        "logs:TagResource",
        "logs:UntagLogGroup",
        "logs:UntagResource",
      ]
    }
    Lambda = {
      arns = ["${local.arn}:lambda:${local.region}:${local.account}:function:{env}-*"]
      read = [
        "lambda:GetFunction",
        "lambda:GetFunctionCodeSigningConfig",
        "lambda:GetFunctionConcurrency",
        "lambda:GetFunctionConfiguration",
        "lambda:GetPolicy",
        "lambda:GetRuntimeManagementConfig",
        "lambda:ListTags",
        "lambda:ListVersionsByFunction",
      ]
      write = [
        "lambda:AddPermission",
        "lambda:CreateFunction",
        "lambda:DeleteFunction",
        "lambda:RemovePermission",
        "lambda:TagResource",
        "lambda:UntagResource",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
      ]
    }
    EventBridge = {
      arns = ["${local.arn}:events:${local.region}:${local.account}:rule/{env}-*"]
      read = ["events:DescribeRule", "events:ListTagsForResource", "events:ListTargetsByRule"]
      write = [
        "events:DeleteRule",
        "events:DisableRule",
        "events:EnableRule",
        "events:PutRule",
        "events:PutTargets",
        "events:RemoveTargets",
        "events:TagResource",
        "events:UntagResource",
      ]
    }
    Glue = {
      arns = [
        "${local.arn}:glue:${local.region}:${local.account}:catalog",
        "${local.arn}:glue:${local.region}:${local.account}:database/{db}",
        "${local.arn}:glue:${local.region}:${local.account}:table/{db}/*",
      ]
      read = ["glue:GetDatabase", "glue:GetTable", "glue:GetTags"]
      write = [
        "glue:CreateDatabase",
        "glue:CreateTable",
        "glue:DeleteDatabase",
        "glue:DeleteTable",
        "glue:TagResource",
        "glue:UntagResource",
        "glue:UpdateDatabase",
        "glue:UpdateTable",
      ]
    }
    # Named queries are authorized against their workgroup.
    Athena = {
      arns = ["${local.arn}:athena:${local.region}:${local.account}:workgroup/{env}-*"]
      read = [
        "athena:BatchGetNamedQuery",
        "athena:GetNamedQuery",
        "athena:GetWorkGroup",
        "athena:ListNamedQueries",
        "athena:ListTagsForResource",
      ]
      write = [
        "athena:CreateNamedQuery",
        "athena:CreateWorkGroup",
        "athena:DeleteNamedQuery",
        "athena:DeleteWorkGroup",
        "athena:TagResource",
        "athena:UntagResource",
        "athena:UpdateNamedQuery",
        "athena:UpdateWorkGroup",
      ]
    }
  }

  # Services whose statements go in the -control policy instead of -services,
  # to keep each managed policy under 6,144 characters.
  control_services = ["Lambda", "EventBridge", "Glue", "Athena"]

  # Calls that take no resource ARN; reads only.
  describe_any = ["cloudwatch:DescribeAlarms", "logs:DescribeLogGroups"]

  iam_role_read = [
    "iam:GetRole",
    "iam:GetRolePolicy",
    "iam:ListAttachedRolePolicies",
    "iam:ListInstanceProfilesForRole",
    "iam:ListRolePolicies",
    "iam:ListRoleTags",
  ]

  apigateway_collections = ["/restapis", "/apikeys", "/usageplans"]

  # Per environment: the name prefix, Glue database and boundary ARN.
  envs = {
    for env in var.environments : env => {
      prefix   = "${var.project}-${env}"
      db       = replace("${var.project}_${env}", "-", "_")
      boundary = "${local.arn}:iam::${local.account}:policy/${var.project}-${env}-workload-boundary"
    }
  }
}

# ---- Identity provider -------------------------------------------------------

# AWS validates GitHub's certificate itself, so no thumbprint is needed.
resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 1 : 0

  url            = "https://${local.github_oidc_url}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1

  url = "https://${local.github_oidc_url}"
}

locals {
  github_oidc_provider_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

# ---- Workload permissions boundaries -----------------------------------------

# The most any role in <project>-<env> may do: the union of what the producer,
# processor, API and Firehose roles need. Each role's own policy narrows it
# further. API Gateway's logging role has a boundary of its own (below).
data "aws_iam_policy_document" "boundary" {
  for_each = local.envs

  statement {
    sid       = "LambdaAndFirehoseLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [for group in ["/aws/lambda", "/aws/kinesisfirehose"] : "${local.arn}:logs:${local.region}:${local.account}:log-group:${group}/${each.value.prefix}-*"]
  }

  statement {
    sid       = "NoResourceArn"
    actions   = ["logs:DescribeLogGroups", "xray:PutTelemetryRecords", "xray:PutTraceSegments"]
    resources = ["*"]
  }

  statement {
    sid = "Stream"
    actions = [
      "kinesis:DescribeStream",
      "kinesis:DescribeStreamSummary",
      "kinesis:GetRecords",
      "kinesis:GetShardIterator",
      "kinesis:ListShards",
      "kinesis:PutRecord",
      "kinesis:PutRecords",
    ]
    resources = ["${local.arn}:kinesis:${local.region}:${local.account}:stream/${each.value.prefix}-*"]
  }

  statement {
    sid       = "LakeBucket"
    actions   = ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads"]
    resources = ["${local.arn}:s3:::${each.value.prefix}-lake-*"]
  }

  statement {
    sid       = "LakeObjects"
    actions   = ["s3:AbortMultipartUpload", "s3:GetObject", "s3:PutObject"]
    resources = ["${local.arn}:s3:::${each.value.prefix}-lake-*/*"]
  }

  statement {
    sid       = "MarketTable"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:UpdateItem"]
    resources = ["${local.arn}:dynamodb:${local.region}:${local.account}:table/${each.value.prefix}-*"]
  }

  statement {
    sid       = "DeadLetterQueue"
    actions   = ["sqs:SendMessage"]
    resources = ["${local.arn}:sqs:${local.region}:${local.account}:${each.value.prefix}-*"]
  }
}

resource "aws_iam_policy" "boundary" {
  for_each = local.envs

  name        = "${each.value.prefix}-workload-boundary"
  description = "Permissions boundary for every role in ${each.value.prefix}"
  policy      = data.aws_iam_policy_document.boundary[each.key].json
}

# API Gateway's account-wide logging role (one per account, in
# apigateway_account_environment). API Gateway refuses a logging role that may
# not write to every log group (UpdateAccount: "The role ARN does not have
# required permissions configured"), so the workload boundary, scoped to the
# project's log groups, cannot be its ceiling. This boundary allows exactly
# what the role's AWS-managed policy grants, and no other role may carry it.
data "aws_iam_policy_document" "apigateway_logs_boundary" {
  statement {
    sid = "ApiGatewayLogs"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
      "logs:FilterLogEvents",
      "logs:GetLogEvents",
      "logs:PutLogEvents",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "apigateway_logs_boundary" {
  name        = "${local.envs[var.apigateway_account_environment].prefix}-apigateway-logs-boundary"
  description = "Permissions boundary for API Gateway's account logging role"
  policy      = data.aws_iam_policy_document.apigateway_logs_boundary.json
}

# ---- Deploy roles ------------------------------------------------------------

data "aws_iam_policy_document" "deploy_trust" {
  for_each = local.envs

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_url}:sub"
      values   = [for subject in var.deploy_subjects[each.key] : "repo:${var.github_repository}:${subject}"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  for_each = local.envs

  name                 = "${var.project}-github-deploy-${each.key}"
  description          = "Terraform apply for ${each.value.prefix} from GitHub Actions"
  assume_role_policy   = data.aws_iam_policy_document.deploy_trust[each.key].json
  max_session_duration = 3600
}

# Create, read, update and delete the environment's resources, service by service.
data "aws_iam_policy_document" "deploy_services" {
  for_each = local.envs

  dynamic "statement" {
    for_each = { for name, service in local.services : name => service if !contains(local.control_services, name) }

    content {
      sid       = "Manage${statement.key}"
      actions   = concat(statement.value.read, statement.value.write)
      resources = [for arn in statement.value.arns : replace(replace(arn, "{env}", each.value.prefix), "{db}", each.value.db)]
    }
  }

  statement {
    sid       = "DescribeAny"
    actions   = local.describe_any
    resources = ["*"]
  }
}

# Lambda, EventBridge and analytics, plus the statements that need conditions:
# state, IAM, event-source mappings and API Gateway.
data "aws_iam_policy_document" "deploy_control" {
  for_each = local.envs

  dynamic "statement" {
    for_each = { for name, service in local.services : name => service if contains(local.control_services, name) }

    content {
      sid       = "Manage${statement.key}"
      actions   = concat(statement.value.read, statement.value.write)
      resources = [for arn in statement.value.arns : replace(replace(arn, "{env}", each.value.prefix), "{db}", each.value.db)]
    }
  }

  statement {
    sid       = "ListStateBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.tfstate.arn]
  }

  # The state file and its lockfile (<key>.tflock) under <env>/.
  statement {
    sid       = "ReadWriteOwnState"
    actions   = ["s3:DeleteObject", "s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/${each.key}/*"]
  }

  statement {
    sid = "ManageRoles"
    actions = concat(local.iam_role_read, [
      "iam:DeleteRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
    ])
    resources = ["${local.arn}:iam::${local.account}:role/${each.value.prefix}-*"]
  }

  # A role can be created, or its permissions changed, only while it carries
  # this environment's boundary.
  statement {
    sid = "ChangeBoundedRoles"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRolePolicy",
      "iam:PutRolePermissionsBoundary",
      "iam:PutRolePolicy",
    ]
    resources = ["${local.arn}:iam::${local.account}:role/${each.value.prefix}-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [each.value.boundary]
    }
  }

  statement {
    sid       = "PassRolesToWorkloads"
    actions   = ["iam:PassRole"]
    resources = ["${local.arn}:iam::${local.account}:role/${each.value.prefix}-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["apigateway.amazonaws.com", "firehose.amazonaws.com", "lambda.amazonaws.com"]
    }
  }

  statement {
    sid       = "CreateEventSourceMapping"
    actions   = ["lambda:CreateEventSourceMapping"]
    resources = ["*"]

    condition {
      test     = "ArnLike"
      variable = "lambda:FunctionArn"
      values   = ["${local.arn}:lambda:${local.region}:${local.account}:function:${each.value.prefix}-*"]
    }
  }

  statement {
    sid = "ManageEventSourceMappings"
    actions = [
      "lambda:DeleteEventSourceMapping",
      "lambda:GetEventSourceMapping",
      "lambda:UpdateEventSourceMapping",
    ]
    resources = ["${local.arn}:lambda:${local.region}:${local.account}:event-source-mapping:*"]

    condition {
      test     = "ArnLike"
      variable = "lambda:FunctionArn"
      values   = ["${local.arn}:lambda:${local.region}:${local.account}:function:${each.value.prefix}-*"]
    }
  }

  # Default tags reach the mapping too; its tag calls take no function condition.
  statement {
    sid       = "TagEventSourceMappings"
    actions   = ["lambda:ListTags", "lambda:TagResource", "lambda:UntagResource"]
    resources = ["${local.arn}:lambda:${local.region}:${local.account}:event-source-mapping:*"]
  }

  # REST API, key and usage plan IDs are random, so API Gateway is scoped by
  # the Environment tag the provider's default_tags put on every resource.
  # Stages, methods and the other children of a REST API are authorized
  # against the API's tags.
  statement {
    sid       = "CreateTaggedApiGateway"
    actions   = ["apigateway:POST"]
    resources = [for path in local.apigateway_collections : "${local.arn}:apigateway:${local.region}::${path}"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Environment"
      values   = [each.key]
    }
  }

  statement {
    sid = "ManageTaggedApiGateway"
    actions = [
      "apigateway:DELETE",
      "apigateway:GET",
      "apigateway:PATCH",
      "apigateway:POST",
      "apigateway:PUT",
    ]
    resources = [for path in concat(local.apigateway_collections, ["/tags"]) : "${local.arn}:apigateway:${local.region}::${path}/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Environment"
      values   = [each.key]
    }
  }

  dynamic "statement" {
    for_each = each.key == var.apigateway_account_environment ? [1] : []

    content {
      sid       = "ApiGatewayAccountLoggingRole"
      actions   = ["apigateway:GET", "apigateway:PATCH"]
      resources = ["${local.arn}:apigateway:${local.region}::/account"]
    }
  }

  # The logging role carries its own boundary instead of the workload one,
  # and takes no inline policy.
  dynamic "statement" {
    for_each = each.key == var.apigateway_account_environment ? [1] : []

    content {
      sid       = "CreateApiGatewayLoggingRole"
      actions   = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"]
      resources = ["${local.arn}:iam::${local.account}:role/${each.value.prefix}-apigateway-logs"]

      condition {
        test     = "StringEquals"
        variable = "iam:PermissionsBoundary"
        values   = [aws_iam_policy.apigateway_logs_boundary.arn]
      }
    }
  }

  # The one AWS-managed policy in the project, on the one role that uses it.
  dynamic "statement" {
    for_each = each.key == var.apigateway_account_environment ? [1] : []

    content {
      sid       = "AttachApiGatewayLoggingPolicy"
      actions   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
      resources = ["${local.arn}:iam::${local.account}:role/${each.value.prefix}-apigateway-logs"]

      condition {
        test     = "ArnEquals"
        variable = "iam:PolicyARN"
        values   = ["${local.arn}:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"]
      }

      condition {
        test     = "StringEquals"
        variable = "iam:PermissionsBoundary"
        values   = [aws_iam_policy.apigateway_logs_boundary.arn]
      }
    }
  }
}

# Managed policies, not inline ones: together they exceed the 10,240-character
# inline limit per role, and each stays under the 6,144-character managed limit.
resource "aws_iam_policy" "deploy_services" {
  for_each = local.envs

  name        = "${var.project}-github-deploy-${each.key}-services"
  description = "Manage the ${each.value.prefix} data, messaging and logging resources"
  policy      = data.aws_iam_policy_document.deploy_services[each.key].json
}

resource "aws_iam_policy" "deploy_control" {
  for_each = local.envs

  name        = "${var.project}-github-deploy-${each.key}-control"
  description = "Lambda, EventBridge, analytics, state, IAM and API Gateway for ${each.value.prefix}"
  policy      = data.aws_iam_policy_document.deploy_control[each.key].json
}

resource "aws_iam_role_policy_attachment" "deploy_services" {
  for_each = local.envs

  role       = aws_iam_role.deploy[each.key].name
  policy_arn = aws_iam_policy.deploy_services[each.key].arn
}

resource "aws_iam_role_policy_attachment" "deploy_control" {
  for_each = local.envs

  role       = aws_iam_role.deploy[each.key].name
  policy_arn = aws_iam_policy.deploy_control[each.key].arn
}

# ---- Plan role ---------------------------------------------------------------

data "aws_iam_policy_document" "plan_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Pull requests from forks get no OIDC token, so this means branches of
    # the repository itself.
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_url}:sub"
      values   = ["repo:${var.github_repository}:pull_request"]
    }
  }
}

resource "aws_iam_role" "plan" {
  name                 = "${var.project}-github-plan"
  description          = "Read-only terraform plan for every environment from pull requests"
  assume_role_policy   = data.aws_iam_policy_document.plan_trust.json
  max_session_duration = 3600
}

# The read half of every deploy statement, across all environments. Plans run
# with -lock=false, so the state is only read. The state holds the API keys;
# only collaborators can open pull requests that get this role.
data "aws_iam_policy_document" "plan" {
  dynamic "statement" {
    for_each = { for name, service in local.services : name => service if length(service.read) > 0 }

    content {
      sid       = "Read${statement.key}"
      actions   = statement.value.read
      resources = [for arn in statement.value.arns : replace(replace(arn, "{env}", "${var.project}-*"), "{db}", "${replace(var.project, "-", "_")}_*")]
    }
  }

  statement {
    sid       = "DescribeAny"
    actions   = local.describe_any
    resources = ["*"]
  }

  statement {
    sid       = "ReadState"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = concat([aws_s3_bucket.tfstate.arn], [for env in var.environments : "${aws_s3_bucket.tfstate.arn}/${env}/*"])
  }

  statement {
    sid       = "ReadRoles"
    actions   = local.iam_role_read
    resources = ["${local.arn}:iam::${local.account}:role/${var.project}-*"]
  }

  statement {
    sid       = "ReadEventSourceMappings"
    actions   = ["lambda:GetEventSourceMapping", "lambda:ListTags"]
    resources = ["${local.arn}:lambda:${local.region}:${local.account}:event-source-mapping:*"]
  }

  statement {
    sid       = "ReadTaggedApiGateway"
    actions   = ["apigateway:GET"]
    resources = [for path in concat(local.apigateway_collections, ["/tags"]) : "${local.arn}:apigateway:${local.region}::${path}/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = [var.project]
    }
  }

  statement {
    sid       = "ReadApiGatewayAccount"
    actions   = ["apigateway:GET"]
    resources = ["${local.arn}:apigateway:${local.region}::/account"]
  }
}

resource "aws_iam_policy" "plan" {
  name        = "${var.project}-github-plan"
  description = "Read what terraform plan refreshes, for every ${var.project} environment"
  policy      = data.aws_iam_policy_document.plan.json
}

resource "aws_iam_role_policy_attachment" "plan" {
  role       = aws_iam_role.plan.name
  policy_arn = aws_iam_policy.plan.arn
}

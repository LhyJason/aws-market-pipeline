terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 6.58.0 waits for a new REST API to report status AVAILABLE, a field
      # LocalStack 4.14 never returns, so apply would fail. envs/dev is not pinned.
      version = ">= 5.70, < 6.58"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }
  }
}

# Everything goes to LocalStack on localhost:4566. "test"/"test" are
# LocalStack's documented dummy credentials, not real keys; no request leaves
# this machine except the producer's call to the public price feed.
provider "aws" {
  region     = "us-east-1"
  access_key = "test"
  secret_key = "test"

  s3_use_path_style           = true
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    apigateway = var.localstack_endpoint
    cloudwatch = var.localstack_endpoint
    dynamodb   = var.localstack_endpoint
    events     = var.localstack_endpoint
    firehose   = var.localstack_endpoint
    iam        = var.localstack_endpoint
    kinesis    = var.localstack_endpoint
    lambda     = var.localstack_endpoint
    logs       = var.localstack_endpoint
    s3         = var.localstack_endpoint
    sqs        = var.localstack_endpoint
    sts        = var.localstack_endpoint
  }

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

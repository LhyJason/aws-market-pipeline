terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 6.0 for data.aws_region.region.
      version = ">= 6.0, < 7.0"
    }
  }
}

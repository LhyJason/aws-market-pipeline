variable "localstack_endpoint" {
  description = "LocalStack edge endpoint"
  type        = string
  default     = "http://localhost:4566"
}

variable "project" {
  type    = string
  default = "market-pipeline"
}

variable "environment" {
  type    = string
  default = "local"
}

variable "symbols" {
  description = "Symbols the producer fetches"
  type        = list(string)
  default     = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
}

variable "feed_base_url" {
  description = "Price feed base URL; use https://api.binance.us from the US"
  type        = string
  default     = "https://api.binance.us"
}

variable "processor_architecture" {
  description = "LocalStack runs Lambdas on the Docker host's architecture; use arm64 on Apple silicon"
  type        = string
  default     = "x86_64"
}

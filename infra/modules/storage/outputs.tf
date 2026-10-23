output "bucket_name" {
  value = aws_s3_bucket.lake.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.lake.arn
}

output "firehose_name" {
  value = aws_kinesis_firehose_delivery_stream.raw.name
}

output "firehose_arn" {
  value = aws_kinesis_firehose_delivery_stream.raw.arn
}

output "firehose_log_group_name" {
  value = aws_cloudwatch_log_group.firehose.name
}

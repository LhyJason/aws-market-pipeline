output "alarm_topic_arn" {
  value = aws_sns_topic.alarms.arn
}

output "dashboard_name" {
  value = aws_cloudwatch_dashboard.pipeline.dashboard_name
}

output "alarm_names" {
  value = [for alarm in local.alarms : alarm.alarm_name]
}

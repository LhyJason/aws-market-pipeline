output "database_name" {
  value = aws_glue_catalog_database.lake.name
}

output "table_name" {
  value = aws_glue_catalog_table.bars_1m.name
}

output "workgroup" {
  value = aws_athena_workgroup.analytics.name
}

output "named_query_ids" {
  value = { for name, query in aws_athena_named_query.saved : name => query.id }
}

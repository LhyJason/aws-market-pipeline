locals {
  # Glue and Athena names allow lowercase letters, digits and underscores.
  database_name = replace(var.name_prefix, "-", "_")
  table         = "bars_1m"
  location      = "s3://${var.lake_bucket}/processed/${local.table}/"

  # Shared with the DDL and the tests, so the three cannot drift apart.
  schema = jsondecode(file("${path.module}/../../../analytics/athena/schema/bars_1m.json"))

  queries = {
    daily_vwap    = "Daily tick-weighted VWAP per symbol, last 7 days"
    largest_moves = "Largest close-to-close 1-minute moves, last 7 days"
    minute_gaps   = "Runs of missing minutes per symbol, last 7 days"
  }
}

# ---- Glue Data Catalog -----------------------------------------------------

resource "aws_glue_catalog_database" "lake" {
  name        = local.database_name
  description = "Market pipeline lake (${var.name_prefix})"
}

# Partition projection: Athena derives partition locations from the table
# properties, so new hours are queryable as soon as the processor writes them.
# No crawler, no MSCK REPAIR, no partitions stored in the catalog.
resource "aws_glue_catalog_table" "bars_1m" {
  name          = local.table
  database_name = aws_glue_catalog_database.lake.name
  description   = "1-minute OHLC bars written by the processor Lambda"
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL                      = "TRUE"
    classification                = "parquet"
    "parquet.compression"         = "SNAPPY"
    "projection.enabled"          = "true"
    "projection.symbol.type"      = "enum"
    "projection.symbol.values"    = join(",", var.symbols)
    "projection.dt.type"          = "date"
    "projection.dt.format"        = "yyyy-MM-dd"
    "projection.dt.range"         = "${var.projection_start_date},NOW"
    "projection.dt.interval"      = "1"
    "projection.dt.interval.unit" = "DAYS"
    "projection.hour.type"        = "integer"
    "projection.hour.range"       = "0,23"
    "projection.hour.digits"      = "2"
    "storage.location.template"   = "${local.location}symbol=$${symbol}/dt=$${dt}/hour=$${hour}/"
  }

  dynamic "partition_keys" {
    for_each = local.schema.partition_keys
    content {
      name    = partition_keys.value.name
      type    = partition_keys.value.type
      comment = partition_keys.value.comment
    }
  }

  storage_descriptor {
    location      = local.location
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = local.schema.columns
      content {
        name    = columns.value.name
        type    = columns.value.type
        comment = columns.value.comment
      }
    }
  }
}

# ---- Athena ----------------------------------------------------------------

resource "aws_athena_workgroup" "analytics" {
  name          = "${var.name_prefix}-analytics"
  description   = "Queries over the market lake; results expire with the lake's athena-results/ lifecycle rule"
  force_destroy = var.force_destroy

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.bytes_scanned_cutoff

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    result_configuration {
      output_location = "s3://${var.lake_bucket}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }

  tags = var.tags
}

resource "aws_athena_named_query" "saved" {
  for_each = local.queries

  name        = each.key
  description = each.value
  workgroup   = aws_athena_workgroup.analytics.id
  database    = aws_glue_catalog_database.lake.name
  query       = file("${path.module}/../../../analytics/athena/queries/${each.key}.sql")
}

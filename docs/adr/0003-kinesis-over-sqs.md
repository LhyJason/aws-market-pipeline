# ADR 0003: Kinesis Data Streams as the ingestion bus

**Status:** accepted · **Date:** 2026-10-23

## Context

The producer emits a few records per minute today but the design should look
like a real streaming system. Candidates: Kinesis Data Streams, SQS (standard
or FIFO), EventBridge bus, direct writes to DynamoDB/S3.

## Decision

Kinesis Data Streams, provisioned mode, one shard, 24-hour retention,
partition key = symbol.

- Ordered, replayable log per shard; multiple consumers can read the same
  stream (the processor Lambda now, Firehose to S3 later) without
  fan-out plumbing.
- Native Lambda event-source mapping with batching, bisect-on-error, and
  iterator-age metrics that are worth showing on a dashboard.
- It is the service interviewers expect when they hear "streaming on AWS".

## Consequences

- The shard is an always-on charge (~$11/month). The schedule can be disabled
  with a variable, and dev can be destroyed between sessions.
- SQS would have been cheaper and simpler; it does not preserve ordering per
  key (standard) or support multiple independent consumers of one message.
- If throughput ever exceeds 1 MB/s or 1000 records/s per shard, switch to
  on-demand mode or add shards; the partition-by-symbol scheme already
  supports that.

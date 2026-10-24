# ADR 0009: Measure on LocalStack only, and label every number

**Status:** accepted · **Date:** 2026-10-24

## Context

Phase 6 fills the README's Results table: events per day, end-to-end
latency, throughput, monthly cost and test coverage. Latency, throughput and
cost only mean something about AWS when measured on AWS. The project has
never been applied to a real account, and the author pays nothing for it
([docs/cost.md](../cost.md)).

A time-boxed AWS run was priced: bootstrap, deploy dev, a 20-minute load
test, the chaos tests, reading CloudWatch and X-Ray, then destroy. It comes
to about $0.75 at list price (about $0.021 an hour for the shard, dashboard,
alarms and metric, plus about $0.65 of S3 PUTs, DynamoDB writes, Firehose
and logs for roughly 228k ticks). Free credits would likely cover it. It
still needs real credentials and a careful teardown, and Cost Explorer only
shows the cost a day later.

Options considered:

| Option | Cost | What the numbers mean |
| --- | --- | --- |
| Run on AWS once, capped | ~$0.75 (cap $5) | real latency, throughput, alarms, traces and an hourly cost to extrapolate |
| **LocalStack only, every number labelled** | $0 | correctness under load, relative behaviour, the processor's ceiling on this laptop; nothing about AWS latency or cost |
| Quote estimates as results | $0 | misleading |

## Decision

- **No AWS run in phase 6.** Every measured number in the README and the
  docs says where it comes from: LocalStack, configuration, an estimate from
  list prices, or `pytest-cov`. Monthly cost stays an estimate and says so.
- **How lag is measured.** It is the processor's `lag_ms`, the same value as
  the `TickLagMs` metric: when a batch's bars have landed, the age of the
  batch's oldest written tick. Percentiles are over batches, so they are an
  upper bound for each batch's ticks.
- **Clock correction.** The load generator runs on the host while LocalStack
  runs in a VM whose clock drifts. The generator reads a few of its own
  records back from Kinesis and subtracts the median arrival-minus-send
  offset, so "lag from arrival" is on one clock.
- **Correctness is the hard result.** A run passes only if the sum of
  `tick_count` over the test symbols' bars equals the ticks sent: nothing
  lost and nothing counted twice. That holds on any platform.
- **What the tooling allows.** `scripts/load_test.py` runs against AWS with
  `ALLOW_LOAD_ON_AWS=1`, so the same measurement can be made there later
  without new code.

## Consequences

- **Not proven:** the Results table can't claim AWS latency, throughput or
  cost. It also can't show that alarms fire, that X-Ray records traces, or
  that IAM and the permissions boundaries are sufficient. Those stay unproven
  until a run on AWS ([tests/integration/README.md](../../tests/integration/README.md)
  lists them).
- **Laptop-bound:** the ceiling measured here (about 70 ticks/s with
  100-record batches) is LocalStack's poll cadence on one laptop. It shows
  that batch size is the lever, not what AWS would do.
- **Upgrading:** to measure on AWS later, approve a capped run. Then replace
  the LocalStack rows that have an AWS equivalent, and keep both columns
  where they differ.

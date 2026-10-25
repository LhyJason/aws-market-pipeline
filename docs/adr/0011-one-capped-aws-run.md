# ADR 0011: One capped run on AWS

**Status:** accepted · **Date:** 2026-10-25 · Supersedes part of
[ADR 0009](0009-measure-on-localstack-only.md)

## Context

[ADR 0009](0009-measure-on-localstack-only.md) kept every measurement on
LocalStack: latency, throughput and cost mean something about AWS only when
measured there, and the project was meant to cost nothing. It priced a
capped AWS run at about $0.75 and named what only such a run could show:
real latency and throughput, alarms, traces, and whether IAM and the
permissions boundaries are enough.

The author's AWS account came with $100 of credits. On the free plan,
Kinesis Data Streams and Firehose aren't available
(`SubscriptionRequiredException`), so the account was upgraded to the paid
plan, which keeps the credits.

## Decision

Run the dev stack on AWS once, with a $5 cap, and tear it down:

- **Guardrail first:** a $5 monthly AWS Budget from `infra/bootstrap`, with
  email alerts at 80% actual and 100% forecast. It stays after the run; it
  costs nothing.
- **Minimal bootstrap:** only the budget and the dev permissions boundary.
  No state bucket and no GitHub roles: state stayed local for the run, and the
  stack was applied by an IAM user with admin rights, not by the deploy role.
- **The plan from ADR 0009:** deploy `infra/envs/dev`, run the integration
  tests, a load sweep from 10 to 800 ticks/s and the chaos tests, read
  CloudWatch, Logs Insights and X-Ray, then `terraform destroy` and check
  every service for leftovers.
- **Label every number.** The README and the docs keep the LocalStack
  numbers and add AWS ones beside them, each marked with where it came from.

## Consequences

- **Measured on AWS:** lag, the processor's ceiling (about 100 records/s on
  one shard, against about 70 on LocalStack), cold starts, API latency, and
  zero loss across 98,550 ticks ([docs/load-test.md](../load-test.md#results-on-aws)).
  The stack was up for 56 minutes.
- **Proven:** the workload roles' policies, under the dev boundary, are
  enough for the producer, processor, Firehose and API, including the
  processor sending a failed batch to the DLQ. Alarms evaluate and X-Ray
  records traces.
- **Found and fixed:**
  - API Gateway refused its account logging role under the workload
    boundary. That role now has a boundary of its own, verified on AWS
    ([infra/bootstrap](../../infra/bootstrap/README.md)).
  - A new API key is refused for about a minute, so the integration tests
    wait for it.
  - The load test's drain timeout ran from the last send, and its clock
    read-back delayed the drain timer by about 20 s; both fixed.
  - The chaos tests didn't trip any alarm: each API test now sends enough
    5xx responses on its own. The DLQ test still deletes its message within
    seconds, before SQS reports it, and the iterator-age alarm needs five
    minutes over its threshold, which the 800 ticks/s run (three minutes)
    didn't reach ([tests/integration/README.md](../../tests/integration/README.md#chaos-tests)).
- **Still not proven:** the GitHub deploy and plan roles (the run used an
  admin user), prod, the API's throttling and quota under load, and the email
  path from SNS (no address was subscribed).
- **Cost:** see [docs/cost.md](../cost.md). AWS reports charges a day late,
  so the measured figure is filled in after the run.

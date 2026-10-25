# Load test and measurements

How the numbers in the README's Results table are measured, what they were
on LocalStack and on AWS, and what each can and cannot say. The AWS numbers
come from one capped run on 2026-10-25
([ADR 0011](adr/0011-one-capped-aws-run.md)); every number says which
platform it is from.

## Tools

| Command | What it does |
| --- | --- |
| `make load RATE=50 DURATION=60 SYMBOLS=5` | `scripts/load_test.py`: pushes ticks onto the stream at a set rate, waits for every one to land in DynamoDB, prints a report, cleans up |
| `make load-sweep RATES="10 50 100 200"` | the load test at each rate in turn |
| `make load-report RUN=<id>` | `scripts/load_report.py`: re-reports a saved run from the processor's logs |
| `make load-report MINUTES=60` | the same report for the last hour of processor logs, e.g. the producer alone |
| `make test` | unit tests with statement and branch coverage of `services/` |

Both scripts read the stack from `terraform output` in `TF_ENV_DIR`
(default `infra/envs/local`) and talk to LocalStack with its dummy
credentials, or to AWS with boto3's usual credentials when `TF_ENV_DIR` is
`infra/envs/dev`. They refuse to touch a stack on AWS unless
`ALLOW_LOAD_ON_AWS=1`.
Set `TERRAFORM` to its full path if it is not on `PATH`.

## What a run does

1. **Pauses the producer's schedule** (its EventBridge rule), so every
   processor batch in the window is load; it is re-enabled afterwards, even
   on failure. `--keep-schedule` leaves it on.
2. **Sends `rate x duration` ticks** in the producer's record format, spread
   round-robin over `LOAD-<run>-<i>` symbols, paced every 50 ms with
   `PutRecords`. Records Kinesis rejects are retried with backoff and
   counted. Each tick of a symbol gets its own millisecond, because the
   processor treats `(symbol, ts)` as a tick's identity.
3. **Waits for every tick to land**, polling the sum of `tick_count` over the
   symbols' bars. The run passes only if it equals the number sent: nothing
   lost, nothing counted twice. It gives up after `--drain-timeout` seconds
   (default 300) in which no new tick lands, so a long backlog that is still
   draining is waited for.
4. **Measures the clock offset** between the generator and Kinesis: it reads
   a few of its records back and compares their `ApproximateArrivalTimestamp`
   with their `ts`. On this Windows machine the WSL2 VM that runs Docker (and
   so LocalStack and the Lambdas) was about half a second ahead of Windows;
   AWS's clock was 1.4 to 2.5 s ahead.
5. **Reports** from the processor's logs, saves the run under
   `.build/load/<run>.json`, and deletes the test symbols from DynamoDB and
   `processed/` (their records stay in `raw/`, which expires after 30 days).

## Definitions

- **Lag** is the processor's `lag_ms` (the same value as the `TickLagMs`
  metric): when a batch's bars have landed, the age of the batch's *oldest*
  written tick. Percentiles are over batches, so each is an upper bound for
  the ticks in its batch. For the producer the clock starts when the price
  feed answered; for the load test, when the generator sent the tick.
- **Lag from arrival** subtracts the measured clock offset, which leaves the
  time from Kinesis accepting a record to its bar landing. It includes the
  one-way trip to Kinesis, a few milliseconds locally.
- **End-to-end throughput** is ticks landed divided by the time from the
  first send to the last tick landing.
- **Duration** is the processor's invocation time from the Lambda REPORT lines.

## Results on LocalStack

Measured 2026-10-24 on the author's Windows 10 laptop: LocalStack 4.14.0 in
Docker Desktop (WSL2), the processor at 512 MB on x86_64, one shard, batch
size 100, `parallelization_factor` 1. The stack was freshly applied, and a
15-second run warmed the processor up first. Each run was 60 seconds over 5
symbols.

| Offered (ticks/s) | Sent at (ticks/s) | Landed | Last landed after last send | Processor pace (records/s) | Batches, mean size | Lag p50 / p95 / max | Invocation p50 / p95 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 10.0 | 600 / 600 | 1.3 s | 10.1 | 45, 13.4 | 1.31 / 1.39 / 1.48 s | 317 / 345 ms |
| 50 | 49.9 | 3,000 / 3,000 | 1.3 s | 49.7 | 45, 66.7 | 1.35 / 1.54 / 1.63 s | 328 / 376 ms |
| 70 | 69.9 | 4,200 / 4,200 | 2.4 s | 69.5 | 45, 93.4 | 1.36 / 1.65 / 1.69 s | 332 / 367 ms |
| 100 | 99.5 | 6,000 / 6,000 | 26.2 s | 71.2 | 61, 98.4 | 12.7 / 23.9 / 25.3 s | 337 / 359 ms |
| 200 | 103.6 | 12,000 / 12,000 | 134.0 s | 48.0 | 122, 98.5 | 125.8 / 172.2 / 180.4 s | 342 / 382 ms |

The generator's clock was 16 to 24 ms behind Kinesis's in every run, so
lag measured from arrival differs from raw lag by about 20 ms. "Landed" means
the bars' `tick_count` matched the number sent exactly. No run had a
malformed record, a failed symbol or a Kinesis retry. The processor's own
batches include the producer's 3 ticks a minute (LocalStack 4.14 keeps firing
the schedule rule while it is disabled).

The producer alone, at its default one run a minute, had a lag of p50 422 ms,
p95 1,023 ms and max 1,047 ms over 37 batches (37 minutes on a fresh stack,
2026-10-24, 0 errors). Its
three ticks arrive together, so its batches' oldest tick is barely older than
the newest. Under load the oldest tick has waited a whole poll cycle, which is
why lag at 10 ticks/s is higher.

### What this shows

- **No loss and no double counting.** Every tick of every run was counted
  exactly once in DynamoDB, including runs where the processor fell minutes
  behind.
- **The processor's ceiling here is about 70 ticks/s.** LocalStack invokes
  the processor about every 1.4 s (the poll interval plus a ~340 ms
  invocation), and a batch holds at most 100 records. At 70 ticks/s the
  batches are nearly full; at 100 they are full and lag grows without bound
  until the run ends. The ceiling is set by batch size times poll rate, not by
  the processor's work: invocation time hardly changed from 13 records to 100.
- **The levers** are `batch_size` (up to 10,000) and the number of shards.
  `parallelization_factor` must stay 1: the bar merges rely on one batch per
  shard at a time.
- **At 200 ticks/s the laptop gave out first.** LocalStack accepted only
  about 104 PutRecords records a second while the processor was busy, and its
  poll cycle slowed to about 2 s, so the processor's pace fell to 48 records/s.
  That is a limit of this machine, not of the pipeline.

### What LocalStack numbers cannot tell us

- **Anything about AWS latency or throughput.** On AWS, Lambda polls each
  shard on its own schedule, invocations run on arm64 in AWS's network, and
  DynamoDB and S3 calls take AWS's time, not a container's on the same
  laptop. The lag and the 70 ticks/s ceiling above are LocalStack's; see
  [Results on AWS](#results-on-aws) for AWS's.
- **Limits.** LocalStack does not enforce Kinesis's per-shard limits
  (1,000 records or 1 MB a second), DynamoDB throttling, or Lambda
  concurrency, so it cannot show where AWS would push back.
- **Cold starts.** The warm-up run absorbed the one cold start; on AWS they
  recur as containers are recycled.
- **Cost.** Nothing here is billed.
- **One machine runs everything,** the generator included, so the results
  also measure this laptop's CPU.

A clock note: before the stack was recreated, Windows and the WSL2 VM that
runs Docker were 550 to 720 ms apart, after hours of uptime. Lag measured
from arrival removes that offset; the raw lag does not.

## Results on AWS

Measured 2026-10-25 on the dev stack in us-east-1, in one capped run
([ADR 0011](adr/0011-one-capped-aws-run.md)), with the same settings as on
LocalStack: the processor at 512 MB (arm64 here), one shard, batch size 100,
`parallelization_factor` 1. The generator ran on the same laptop and sent
over the internet. A 15-second run warmed the processor up first; each run
was 60 seconds over 5 symbols.

| Offered (ticks/s) | Sent at (ticks/s) | Landed | Processor pace (records/s) | Batches, mean size | Lag from arrival p50 / p95 / max | Invocation p50 / p95 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 9.7 | 600 / 600 | 10.1 | 30, 20.0 | 1.13 / 2.08 / 2.12 s | 738 / 809 ms |
| 50 | 48.6 | 3,000 / 3,000 | 50.4 | 36, 83.3 | 1.01 / 2.02 / 2.69 s | 806 / 913 ms |
| 70 | 66.9 | 4,200 / 4,200 | 67.5 | 61, 68.9 | 1.83 / 2.60 / 2.81 s | 806 / 882 ms |
| 100 | 94.9 | 6,000 / 6,000 | 97.1 | 68, 88.2 | 2.18 / 2.99 / 3.20 s | 842 / 930 ms |
| 200 | 186.9 | 12,000 / 12,000 | 110.3 | 121, 99.2 | 21.7 / 45.8 / 49.8 s | 880 / 990 ms |
| 400 | 304.0 | 24,000 / 24,000 | 104.3 | 241, 99.6 | 74.6 / 153.5 / 164.0 s | 951 / 1,119 ms |
| 800 | 277.1 | 48,000 / 48,000\* | 89.9\* | 426\*, 99.9 | 166.6 / 361.2 / 389.3 s\* | 1,090 / 1,338 ms\* |

\* The 800 run outlasted the script's drain timeout, which then counted from
the last send: it reported 43,397 of 48,000 landed and summarised the first
426 batches. The processor's logs show all 48,000 (plus 3 producer ticks)
processed in 481 batches, the last about 6.5 minutes after the last send and
before the script deleted the test symbols. The timeout now counts from the
last tick that landed.

Lag is quoted from Kinesis arrival because AWS's clock was 1.4 to 2.5 s ahead
of the laptop's; raw lag is that much higher (p50 2.7 s at 50 ticks/s). The
report's "last landed after last send" and end-to-end throughput are left
out: the script read records back for the clock offset before it started
waiting, which took about 20 s on AWS and inflated both. It now waits first.
No run had a Kinesis retry, a malformed record or a failed symbol.

Before and between the load runs, the producer alone (3 ticks a minute) had a
lag of p50 1.18 s and p95 2.69 s over 36 batches; one batch that waited behind
a backlog reached 45 s. That lag starts when the price feed answered, on the
Lambda's own clock, so no correction applies.

Other measurements from the same run:

- **Cold starts:** 3 of 1,094 processor invocations, each with an init of
  about 630 ms. The processor never used more than 163 MB of its 512 MB.
- **API:** the API Lambda took 16 ms at p50 when warm (87 calls; p95 668 ms
  from 3 cold starts with an init of about 1.1 s). From the laptop, 40 calls
  each to `/prices` and `/bars` took 155 and 168 ms at p50, round trip.
- **Tracing:** X-Ray recorded 194 sampled traces of the producer, processor
  and API, the chaos tests' 502s and 503s among them as faults and errors.
- **Tests:** all 19 integration tests and the 4 chaos tests passed against
  the stack.

### What this shows

- **No loss and no double counting on AWS either,** across 98,550 ticks in 8
  runs, including one where the processor fell more than six minutes behind.
- **The processor's ceiling on AWS is about 100 records/s per shard,** against
  about 70 on LocalStack. Lambda invokes it again as soon as a batch is done,
  about once a second, and an invocation takes 0.7 to 1.1 s whatever the
  batch size (738 ms for 20 records, 951 ms for 100). Batches fill up near
  100 ticks/s and the pace levels off at 90 to 110 records/s. As on
  LocalStack, the levers are `batch_size` and the number of shards.
- **Kinesis was never the limit.** One shard accepts 1,000 records a second;
  it took everything the laptop could send (about 300 ticks/s) without
  rejecting a record.
- **Invocations take longer than on LocalStack** (about 0.8 s against
  0.33 s), presumably the DynamoDB and S3 round trips, which LocalStack
  answers on the same machine. X-Ray's per-call segments would confirm it;
  they weren't analysed.

## Unit test coverage

`make test` measures statements and branches of the Lambda code in
`services/`: 217 tests, **95%** (779 statements, 154 branches). The misses are
the producer's and API's copies of the JSON log formatter (the processor's
copy is tested), two of the feed's error branches, and the producer's
Lambda entry point. The load scripts in `scripts/` are tested too, at 88 to 89%,
but are not part of that figure.

import json

import pytest

import load_report
from load_report import Batch, Run, Spread, Summary


def event(message: str, timestamp: int = 1_000) -> dict:
    return {"message": message, "timestamp": timestamp}


def batch_line(records: int, lag_ms: int | None, **extra) -> str:
    line = {"msg": "processed batch", "records": records, "malformed": 0, "failed_symbols": 0}
    return json.dumps({**line, "lag_ms": lag_ms, **extra})


def run(**changes) -> Run:
    values = {
        "run_id": "ABC123",
        "env_dir": "infra/envs/local",
        "local": True,
        "rate": 50.0,
        "duration_s": 60.0,
        "symbols": ["LOAD-ABC123-0", "LOAD-ABC123-1"],
        "sent": 3000,
        "put_retries": 0,
        "put_dropped": 0,
        "started_ms": 1_000_000,
        "sent_ms": 1_060_000,
        "landed": 3000,
        "landed_ms": 1_062_000,
        "skew_ms": 500.0,
    }
    return Run(**{**values, **changes})


# ---- the logs -------------------------------------------------------------------


def test_log_lines_reads_one_line_per_event_as_on_aws():
    events = [event('{"a": 1}\n', 10), event('{"b": 2}\n', 20), event("START RequestId: x\n")]
    assert list(load_report.log_lines(events)) == [(10, {"a": 1}), (20, {"b": 2})]


def test_log_lines_splits_localstacks_concatenated_lines():
    # LocalStack can put two log lines in one event and append END to the last.
    text = '{"a": 1}\n{"b": 2}END RequestId: x\n'
    assert list(load_report.log_lines([event(text, 5)])) == [(5, {"a": 1}), (5, {"b": 2})]


def test_log_lines_stops_at_broken_json():
    assert list(load_report.log_lines([event('{"a": 1} {"b": ')])) == [(1_000, {"a": 1})]


def test_batches_keeps_only_processed_batch_lines():
    events = [
        event(batch_line(3, 120), 10),
        event(json.dumps({"msg": "dropping malformed record"})),
        event(json.dumps({"_aws": {}, "TickLagMs": 120})),
        event(batch_line(0, None, malformed=2, failed_symbols=1), 20),
    ]
    assert load_report.batches(events) == [
        Batch(at_ms=10, records=3, malformed=0, failed_symbols=0, lag_ms=120),
        Batch(at_ms=20, records=0, malformed=2, failed_symbols=1, lag_ms=None),
    ]


def test_durations_reads_report_lines_only():
    events = [
        event("REPORT RequestId: a\tDuration: 339.61 ms\tBilled Duration: 340 ms\t\n"),
        event("REPORT RequestId: b\tInit Duration: 900.00 ms\tDuration: 12.5 ms\n"),
        event("START RequestId: c Version: $LATEST\n"),
        event(batch_line(1, 5)),
    ]
    # Init Duration comes first on a cold start; only the plain Duration counts.
    assert load_report.durations_ms(events) == [339.61]


def test_fetch_events_follows_every_page():
    class Logs:
        def __init__(self):
            self.calls = []

        def filter_log_events(self, **kwargs):
            self.calls.append(kwargs)
            if "nextToken" not in kwargs:
                return {"events": [event("1")], "nextToken": "t1"}
            if kwargs["nextToken"] == "t1":
                return {"events": [event("2")], "nextToken": "t2"}
            return {"events": [event("3")], "nextToken": "t2"}  # same token: done

    logs = Logs()
    found = load_report.fetch_events(logs, "/aws/lambda/p", 100, 200)
    assert [e["message"] for e in found] == ["1", "2", "3"]
    assert logs.calls[0] == {"logGroupName": "/aws/lambda/p", "startTime": 100, "endTime": 200}


# ---- the numbers ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("p", "expected"),
    [(50, 5), (95, 10), (100, 10), (10, 1), (0, 1)],
)
def test_percentile_is_nearest_rank(p, expected):
    assert load_report.percentile(range(10, 0, -1), p) == expected


def test_percentile_of_nothing_is_an_error():
    with pytest.raises(ValueError):
        load_report.percentile([], 50)


def test_spread():
    assert Spread.of([]) is None
    spread = Spread.of([100.0, 200.0, 300.0])
    assert spread == Spread(200.0, 300.0, 300.0)
    assert spread.shifted(-50) == Spread(150.0, 250.0, 250.0)
    assert str(Spread(1234.4, 5000.0, 6000.0)) == "p50 1,234  p95 5,000  max 6,000"


def test_summary_of_batches():
    found = [
        Batch(10, 100, 0, 0, 900),
        Batch(30, 40, 2, 1, None),  # nothing written, so no lag
        Batch(20, 60, 0, 0, 300),
    ]
    summary = Summary.of(found, [100.0, 300.0])
    assert summary == Summary(
        batches=3,
        records=200,
        max_batch=100,
        malformed=2,
        failed_symbols=1,
        first_ms=10,
        last_ms=30,
        lag=Spread(300, 900, 900),
        duration=Spread(100.0, 300.0, 300.0),
    )


def test_summary_of_nothing():
    summary = Summary.of([], [])
    assert (summary.batches, summary.first_ms, summary.lag, summary.duration) == (
        0,
        None,
        None,
        None,
    )
    assert load_report.format_summary(summary)[0] == "processed   no batches logged in the window"


def test_format_summary_shows_lag_from_kinesis_arrival_when_the_skew_is_known():
    summary = Summary.of([Batch(10, 50, 0, 0, 1500), Batch(2_010, 50, 0, 0, 500)], [200.0])
    lines = load_report.format_summary(summary, skew_ms=400)
    assert lines[0] == "processed   100 records in 2 batches (mean 50.0, max 50)"
    # 100 records between batches logged at 10 and 2,010 ms.
    assert lines[1] == "  pace      50.0 records/s from the first batch to the last (2.0 s)"
    assert lines[2] == "errors      0 malformed records, 0 failed symbols"
    assert lines[3].startswith("lag ms      p50 500  p95 1,500  max 1,500")
    assert lines[4].startswith("  from arrival p50 100  p95 1,100  max 1,100")
    assert lines[5].startswith("duration ms p50 200")
    assert not any("from arrival" in line for line in load_report.format_summary(summary))


def test_format_run_when_every_tick_landed():
    text = load_report.format_run(run(), Summary.of([], []))
    assert "load test ABC123 on infra/envs/local (LocalStack)" in text
    assert "3,000 ticks over 2 symbols, 50.0 ticks/s achieved" in text
    assert "landed      3,000 of 3,000 ticks in DynamoDB bars, the last 2.0 s after" in text
    assert "throughput  48.4 ticks/s end to end" in text  # 3000 / 62 s


def test_format_run_warns_about_a_backlog_but_not_a_few_producer_ticks():
    few = Summary.of([Batch(10, 3009, 0, 0, 100)], [])
    many = Summary.of([Batch(10, 12_584, 0, 0, 100)], [])
    assert "WARNING" not in load_report.format_run(run(), few)
    assert "handled 9,584 records more than this run sent" in load_report.format_run(run(), many)


def test_format_run_when_the_wait_timed_out():
    text = load_report.format_run(run(landed=2500, landed_ms=None, local=False), Summary.of([], []))
    assert "(AWS)" in text
    assert "landed      2,500 of 3,000 ticks: TIMED OUT waiting" in text
    assert "throughput" not in text


def test_run_window_is_on_the_processors_clock():
    assert load_report.run_window(run(), 500.0) == (999_500, 1_064_500)
    assert load_report.run_window(run(landed_ms=None)) == (999_000, 1_362_000)


def test_run_round_trips_through_json(tmp_path):
    saved = run()
    path = saved.save(tmp_path)
    assert path == tmp_path / "ABC123.json"
    assert Run.load("ABC123", tmp_path) == saved


# ---- the stack -------------------------------------------------------------------


def test_connect_uses_localstack_for_envs_local(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setattr(load_report, "terraform_outputs", lambda env_dir: {"market_table": "t"})
    target = load_report.connect("infra/envs/local")
    assert target.is_local
    assert target.endpoint_url == "http://localhost:4566"
    assert target.session.get_credentials().access_key == "test"


def test_connect_uses_aws_for_other_environments(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setattr(load_report, "terraform_outputs", lambda env_dir: {})
    target = load_report.connect("infra/envs/dev")
    assert not target.is_local
    assert target.endpoint_url is None


def test_terraform_outputs_needs_terraform(monkeypatch, tmp_path):
    monkeypatch.delenv("TERRAFORM", raising=False)
    monkeypatch.setattr(load_report.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="terraform not found"):
        load_report.terraform_outputs(tmp_path)

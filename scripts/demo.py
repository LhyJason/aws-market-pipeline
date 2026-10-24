"""A guided run of the whole pipeline on LocalStack, from nothing to teardown, for $0.

    python scripts/demo.py                    # about 5 minutes once Docker images are cached
    python scripts/demo.py --keep             # leave the stack running afterwards
    python scripts/demo.py --record docs/demo/demo.cast   # also write an asciicast

Steps: check the tools, build the Lambda packages, start LocalStack, apply
infra/envs/local, put a live tick through producer -> Kinesis -> processor ->
DynamoDB -> API, run a 20-second load test, break the stack twice with the
chaos tests (poison records, a bad deploy) and watch it recover, query the
Parquet lake with DuckDB, and tear everything down.

Needs Docker (running), Terraform >= 1.10 and Python 3.12 with
requirements-dev.txt installed. Set TERRAFORM to terraform's full path if it
is not on PATH. ``--record`` captures what is printed, as it is printed, in
asciicast v2 format; ``agg`` turns that into a GIF.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TextIO

import load_report
import load_test
import query_lake

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "infra" / "envs" / "local"
CONTAINER = "market-pipeline-localstack"
WIDTH, HEIGHT = 100, 32


class Console:
    """Writes to the terminal, and to an asciicast file when recording."""

    def __init__(self, out: TextIO, cast: TextIO | None = None) -> None:
        self.out = out
        self.cast = cast
        self.started = time.monotonic()
        if cast:
            header = {"version": 2, "width": WIDTH, "height": HEIGHT, "timestamp": int(time.time())}
            cast.write(json.dumps({**header, "title": "aws-market-pipeline on LocalStack"}) + "\n")

    def write(self, text: str) -> int:
        self.out.write(text)
        self.out.flush()
        if self.cast and text:
            elapsed = round(time.monotonic() - self.started, 3)
            self.cast.write(json.dumps([elapsed, "o", text.replace("\n", "\r\n")]) + "\n")
            self.cast.flush()
        return len(text)

    def flush(self) -> None:
        self.out.flush()

    def line(self, text: str = "") -> None:
        self.write(text + "\n")


class Demo:
    def __init__(self, console: Console, *, verbose: bool) -> None:
        self.console = console
        self.verbose = verbose
        self.timings: list[tuple[str, float]] = []

    @contextlib.contextmanager
    def step(self, title: str) -> Iterator[None]:
        number = len(self.timings) + 1
        self.console.line(f"\n==> {number}. {title}")
        started = time.monotonic()
        yield
        took = time.monotonic() - started
        self.timings.append((title, took))
        self.console.line(f"    ({took:.0f} s)")

    def run(
        self,
        command: list[str],
        *,
        cwd: Path = ROOT,
        env: dict[str, str] | None = None,
        show: Callable[[str], bool] = lambda line: True,
    ) -> None:
        """Run a command, printing the lines ``show`` picks (all with --verbose) as they come."""
        printable = " ".join(Path(command[0]).name if i == 0 else a for i, a in enumerate(command))
        self.console.line(f"    $ {printable}")
        tail: list[str] = []
        with subprocess.Popen(
            command,
            cwd=cwd,
            env={**os.environ, **(env or {})},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as process:
            assert process.stdout is not None
            for line in process.stdout:
                tail = [*tail[-39:], line]
                if self.verbose or show(line):
                    self.console.write("    " + line)
        if process.returncode != 0:
            if not self.verbose:
                self.console.write("".join("    " + line for line in tail))
            raise SystemExit(f"failed ({process.returncode}): {printable}")

    @contextlib.contextmanager
    def captured(self) -> Iterator[None]:
        """Send what in-process scripts print to the console, so it is recorded too."""
        with contextlib.redirect_stdout(self.console):  # type: ignore[type-var]
            yield

    def summary(self) -> None:
        total = sum(took for _, took in self.timings)
        self.console.line("\n==> done")
        for title, took in self.timings:
            self.console.line(f"    {took:6.0f} s  {title}")
        self.console.line(f"    {total:6.0f} s  total")


def terraform_bin() -> str:
    found = os.environ.get("TERRAFORM") or shutil.which("terraform")
    if not found:
        raise SystemExit("terraform not found: put it on PATH or set TERRAFORM to its full path")
    return found


def container_running() -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def get(url: str, key: str | None) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers={"x-api-key": key} if key else {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw.decode("utf-8", "replace")


def shorten(body: Any, width: int = WIDTH - 12) -> str:
    text = body if isinstance(body, str) else json.dumps(body, separators=(", ", ": "))
    return text if len(text) <= width else text[: width - 3] + "..."


def interesting(*needles: str) -> Callable[[str], bool]:
    return lambda line: any(needle in line for needle in needles)


def check_tools(demo: Demo, terraform: str) -> None:
    if sys.version_info < (3, 12):  # noqa: UP036 - a reviewer may run it with any Python
        raise SystemExit(f"Python 3.12 or newer is needed, this is {sys.version.split()[0]}")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        raise SystemExit("Docker is not running: start Docker Desktop (or the daemon) first")
    version = subprocess.run([terraform, "version"], capture_output=True, text=True, check=True)
    demo.console.line(
        f"    Python {sys.version.split()[0]}, {version.stdout.splitlines()[0]}, Docker running"
    )


def build(demo: Demo, arch: str) -> None:
    for service in ("processor", "api"):
        demo.run(
            [sys.executable, "scripts/build_lambda.py", service, "--arch", arch],
            show=interesting("built"),
        )


def start_localstack(demo: Demo) -> None:
    if not container_running():
        # LocalStack keeps nothing between runs, so old state describes nothing.
        for stale in LOCAL.glob("terraform.tfstate*"):
            stale.unlink()
    demo.run(["docker", "compose", "up", "-d", "--wait"], cwd=LOCAL, show=interesting("Healthy"))


def apply(demo: Demo, terraform: str, arch: str) -> dict[str, Any]:
    demo.run([terraform, "init", "-input=false"], cwd=LOCAL, show=interesting("initialized"))
    demo.run(
        [terraform, "apply", "-auto-approve", "-input=false", f"-var=lambda_architecture={arch}"],
        cwd=LOCAL,
        show=interesting("Apply complete", "Error"),
    )
    return load_report.terraform_outputs(LOCAL)


def live_tick(demo: Demo, target: load_report.Target) -> None:
    url, key = target.outputs["api_url"], target.outputs["api_key"]
    before = load_test.now_ms()
    function = target.outputs["producer_function_name"]
    response = target.client("lambda").invoke(FunctionName=function)
    demo.console.line(f"    invoked {function}: {response['Payload'].read().decode()}")

    deadline = time.monotonic() + 120
    while True:
        status, body = get(f"{url}/prices/BTCUSDT", key)
        if status == 200 and body["ts"] >= before - 1_000:
            break
        if time.monotonic() > deadline:
            raise SystemExit(f"the tick did not reach the API in 2 minutes: {status} {body}")
        time.sleep(1)
    demo.console.line(
        f"    tick reached the API {(load_test.now_ms() - before) / 1000:.1f} s after the invoke"
    )

    for path, with_key in [
        ("/health", False),
        ("/prices/BTCUSDT", True),
        ("/bars/BTCUSDT?limit=2&window=30", True),
        ("/prices/BTCUSDT", False),
    ]:
        status, body = get(url + path, key if with_key else None)
        label = f"GET {path}" + ("" if with_key else " (no key)")
        demo.console.line(f"    {label:<42} {status}  {shorten(body, WIDTH - 52)}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true", help="leave LocalStack and the stack up")
    parser.add_argument("--record", type=Path, help="also write an asciicast v2 file here")
    parser.add_argument(
        "--arch",
        default="x86_64",
        choices=["x86_64", "arm64"],
        help="the Docker host's (arm64 on Apple silicon)",
    )
    parser.add_argument("--no-build", action="store_true", help="reuse .build/ packages")
    parser.add_argument("--verbose", action="store_true", help="print every line of every command")
    args = parser.parse_args(argv)

    with contextlib.ExitStack() as stack:
        cast = None
        if args.record:
            args.record.parent.mkdir(parents=True, exist_ok=True)
            cast = stack.enter_context(args.record.open("w", encoding="utf-8", newline="\n"))
        demo = Demo(Console(sys.stdout, cast), verbose=args.verbose)
        terraform = terraform_bin()
        os.environ["TERRAFORM"] = terraform  # for the scripts and tests run below

        demo.console.line("aws-market-pipeline: the whole pipeline on LocalStack, $0")
        with demo.step("Check the tools"):
            check_tools(demo, terraform)
        if not args.no_build:
            with demo.step("Build the processor (DuckDB) and API (pydantic-core) packages"):
                build(demo, args.arch)
        try:
            with demo.step("Start LocalStack 4.14 in Docker"):
                start_localstack(demo)
            with demo.step("Apply infra/envs/local: Kinesis, Firehose, Lambdas, DynamoDB, S3, API"):
                outputs = apply(demo, terraform, args.arch)
            target = load_report.connect("infra/envs/local")
            with demo.step(
                "One live tick: feed -> producer -> Kinesis -> processor -> DynamoDB -> API"
            ):
                live_tick(demo, target)
            with (
                demo.step("Load test: 20 ticks/s for 20 s, every tick counted once"),
                demo.captured(),
            ):
                load_test.main(["--rate", "20", "--duration", "20"])
            with demo.step("Chaos: poison records on the stream, then a bad API deploy"):
                demo.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-m",
                        "chaos",
                        "tests/integration",
                        "-v",
                        "-k",
                        "poison or bad_deploy",
                        "-p",
                        "no:cacheprovider",
                    ],
                    env={"RUN_INTEGRATION": "1"},
                    show=interesting("PASSED", "FAILED", "ERROR", " passed", " failed"),
                )
            with (
                demo.step(
                    "Analytics: the saved Athena queries, run on the Parquet lake with DuckDB"
                ),
                demo.captured(),
            ):
                query_lake.main(["--localstack", "--bucket", outputs["lake_bucket"], "daily_vwap"])
        finally:
            if args.keep:
                demo.console.line(
                    "\n    left running: terraform -chdir=infra/envs/local output api_url; "
                    "make local-down to stop"
                )
            else:
                with demo.step("Tear down: stop LocalStack and drop its state"):
                    demo.run(["docker", "compose", "down"], cwd=LOCAL, show=interesting("Removed"))
                    for state in LOCAL.glob("terraform.tfstate*"):
                        state.unlink()
        demo.summary()


if __name__ == "__main__":
    main()

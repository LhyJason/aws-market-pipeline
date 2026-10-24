import io
import json
import sys

import pytest

import demo
from demo import Console, Demo, Indented


def cast_events(cast: io.StringIO) -> tuple[dict, list[list]]:
    header, *events = cast.getvalue().splitlines()
    return json.loads(header), [json.loads(event) for event in events]


def test_console_writes_to_the_terminal_and_records_an_asciicast():
    out, cast = io.StringIO(), io.StringIO()
    console = Console(out, cast)
    console.line("hello")
    console.write("")

    assert out.getvalue() == "hello\n"
    header, events = cast_events(cast)
    assert header["version"] == 2
    assert (header["width"], header["height"]) == (demo.WIDTH, demo.HEIGHT)
    # Terminals need a carriage return too; empty writes are not recorded.
    [[elapsed, kind, text]] = events
    assert (kind, text) == ("o", "hello\r\n")
    assert elapsed >= 0


def test_console_without_a_recording():
    out = io.StringIO()
    assert Console(out).write("x") == 1
    assert out.getvalue() == "x"


def test_indented_prefixes_every_line_even_across_writes():
    out = io.StringIO()
    indented = Indented(Console(out))
    indented.write("one\ntw")
    indented.write("o\n\nthree")
    indented.flush()
    assert out.getvalue() == "    one\n    two\n\n    three"


def test_step_times_each_step_and_summary_adds_them_up(monkeypatch):
    out = io.StringIO()
    run = Demo(Console(out), verbose=False)
    clock = iter([0.0, 2.0, 10.0, 15.0])
    monkeypatch.setattr(demo.time, "monotonic", lambda: next(clock))
    with run.step("First"):
        pass
    with run.step("Second"):
        pass
    run.summary()

    assert run.timings == [("First", 2.0), ("Second", 5.0)]
    text = out.getvalue()
    assert "==> 1. First" in text
    assert "==> 2. Second" in text
    assert "       7 s  total" in text


def script(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_run_prints_only_the_lines_asked_for():
    out = io.StringIO()
    Demo(Console(out), verbose=False).run(
        script("print('noise'); print('Apply complete!')"), show=demo.interesting("Apply")
    )
    assert "    Apply complete!" in out.getvalue()
    assert "    noise\n" not in out.getvalue()


def test_run_prints_everything_when_verbose():
    out = io.StringIO()
    Demo(Console(out), verbose=True).run(script("print('noise')"), show=lambda line: False)
    assert "    noise" in out.getvalue()


def test_run_shows_the_output_of_a_failing_command():
    out = io.StringIO()
    with pytest.raises(SystemExit, match="failed"):
        Demo(Console(out), verbose=False).run(
            script("print('why it broke'); raise SystemExit(3)"), show=lambda line: False
        )
    assert "why it broke" in out.getvalue()


def test_captured_output_is_indented_and_recorded(capsys):
    out, cast = io.StringIO(), io.StringIO()
    run = Demo(Console(out, cast), verbose=False)
    with run.captured():
        print("from a script")
    assert out.getvalue() == "    from a script\n"
    assert capsys.readouterr().out == ""
    _, events = cast_events(cast)
    assert "".join(text for _, _, text in events) == "    from a script\r\n"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"status": "ok"}, '{"status": "ok"}'),
        ("Forbidden", "Forbidden"),
        ({"text": "x" * 50}, '{"text": "' + "x" * 7 + "..."),
    ],
)
def test_shorten(body, expected):
    assert demo.shorten(body, width=20) == expected


def test_interesting():
    picks = demo.interesting("PASSED", "Error:")
    assert picks("test_x PASSED")
    assert picks("Error: no")
    assert not picks('"Errors",')


def test_terraform_bin(monkeypatch):
    monkeypatch.setenv("TERRAFORM", "C:/tools/terraform.exe")
    assert demo.terraform_bin() == "C:/tools/terraform.exe"
    monkeypatch.delenv("TERRAFORM")
    monkeypatch.setattr(demo.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="terraform not found"):
        demo.terraform_bin()

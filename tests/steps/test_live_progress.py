"""Terminal and batch progress reporting."""

import io
import logging
import sys
from collections.abc import Generator
from threading import Event
from time import perf_counter

import pytest

from weavehr.logging import _StepProgress, configure_logging, get_logger, set_log_level


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> Generator[Terminal]:
    stream = Terminal()
    monkeypatch.setenv("TERM", "xterm")
    package = logging.getLogger("weavehr")
    monkeypatch.setattr(package, "handlers", [])
    monkeypatch.setattr(package, "propagate", True)
    level = package.level
    yield stream
    package.setLevel(level)


@pytest.mark.parametrize("shared", [True, False], ids=["same-terminal", "redirected-stderr"])
def test_live_output_and_cleanup_on_failure(terminal: Terminal, monkeypatch: pytest.MonkeyPatch, shared: bool) -> None:
    stderr = terminal if shared else io.StringIO()
    monkeypatch.setattr(sys, "stdout", terminal)
    monkeypatch.setattr(sys, "stderr", stderr)
    configure_logging(format="%(message)s")
    progress = _StepProgress()
    logger = get_logger("test")
    with pytest.raises(ValueError, match="schema"), progress.display("extraction", perf_counter()):
        assert (sys.stderr is not stderr) == shared
        tables = progress.track("testdb/1.0", "tables", 2, ["vitals", "measurements"], str)
        assert next(tables) == "vitals"
        assert next(tables) == "measurements"
        assert progress.detail == "testdb/1.0 | measurements | 1/2 tables processed"
        logger.warning("Missing source file")
        print("stdout diagnostic")
        print("missing units", file=sys.stderr)
        sys.stderr.write("final diagnostic [/literal]")
        raise ValueError("schema")
    logger.info("after failure")
    output = terminal.getvalue()
    assert sys.stderr is stderr and sys.stdout is terminal
    assert stderr.getvalue().count("missing units") == 1
    assert stderr.getvalue().count("final diagnostic [/literal]") == 1
    assert ("missing units" in output) == shared
    assert progress.detail in output and "\x1b[?25h" in output
    assert output.count("Missing source file\n") == output.count("stdout diagnostic\n") == 1
    assert output.endswith("after failure\n")


@pytest.mark.parametrize("mode", ["disabled", "debug", "warning"])
def test_progress_can_be_suppressed(terminal: Terminal, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(sys, "stdout", terminal)
    configure_logging(format="%(message)s", live_progress=mode != "disabled")
    if mode != "disabled":
        set_log_level("DEBUG" if mode == "debug" else "WARNING")
    with _StepProgress().display("extraction", perf_counter()):
        get_logger("test").warning("Missing source file")
    assert terminal.getvalue() == "Missing source file\n"


@pytest.mark.parametrize("mode", ["redirected", "application", "terminal-without-rich"])
def test_plain_logging_reports_a_long_operation(terminal: Terminal, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(sys, "stdout", terminal)
    if mode == "terminal-without-rich":
        monkeypatch.setitem(sys.modules, "rich", None)
        monkeypatch.setitem(sys.modules, "rich.console", None)
    else:
        monkeypatch.setattr(terminal, "isatty", lambda: False)
    package = logging.getLogger("weavehr")
    if mode == "application":
        package.addHandler(logging.StreamHandler(terminal))
        package.setLevel(logging.INFO)
    else:
        configure_logging(format="%(message)s")

    progress = _StepProgress()
    with progress.display("short", perf_counter()):
        get_logger("test").warning("Missing source file")
    assert terminal.getvalue() == "Missing source file\n"

    if mode == "application":
        monkeypatch.setattr("weavehr.logging._DEFAULT_PROGRESS_INTERVAL", 0.01)
    else:
        configure_logging(format="%(message)s", progress_interval=0.01)
    reported = Event()

    def observe(record: logging.LogRecord) -> bool:
        if record.name == "weavehr.progress" and "chartevents" in record.getMessage():
            reported.set()
        return True

    package.handlers[0].addFilter(observe)
    with progress.display("extraction", perf_counter()):
        progress.detail = "mimic-iv/2.2 | chartevents | 1/19 tables processed"
        assert reported.wait(5)
    assert progress.detail in terminal.getvalue()
    assert "\x1b" not in terminal.getvalue()


def test_application_can_silence_periodic_progress(
    monkeypatch: pytest.MonkeyPatch, terminal: Terminal, caplog: pytest.LogCaptureFixture
) -> None:
    package = logging.getLogger("weavehr")
    package.setLevel(logging.INFO)
    package.addHandler(logging.StreamHandler(terminal))
    monkeypatch.setattr("weavehr.logging._log_periodically", lambda *_: pytest.fail("started a progress reporter"))
    with caplog.at_level(logging.WARNING, logger="weavehr.progress"), _StepProgress().display("step", perf_counter()):
        get_logger("test").info("summary")
    assert terminal.getvalue() == "summary\n"


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf")])
def test_invalid_progress_interval_preserves_handlers(interval: float) -> None:
    package = logging.getLogger("weavehr")
    handlers = list(package.handlers)
    with pytest.raises(ValueError, match="progress_interval"):
        configure_logging(progress_interval=interval)
    assert package.handlers == handlers

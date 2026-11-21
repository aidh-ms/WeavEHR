import io
import logging
import sys
from time import perf_counter

import pytest

from weavehr.logging import StepProgress, configure_logging, get_logger, set_log_level


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def terminal(monkeypatch):
    stream = Terminal()
    monkeypatch.setenv("TERM", "xterm")
    package = logging.getLogger("weavehr")
    monkeypatch.setattr(package, "handlers", [])
    monkeypatch.setattr(package, "propagate", True)
    level = package.level
    yield stream
    package.setLevel(level)


def test_live_progress_preserves_warning_and_restores_cursor_on_error(terminal, monkeypatch):
    monkeypatch.setattr(sys, "stdout", terminal)
    configure_logging(format="%(message)s")
    progress = StepProgress()
    logger = get_logger("test")
    with pytest.raises(ValueError, match="schema"):
        with progress.display("extraction", perf_counter()):
            progress.detail = "testdb/1.0 | measurements | 1/2 tables processed"
            logger.warning("Missing source file")
            raise ValueError("schema")
    logger.info("after failure")
    output = terminal.getvalue()
    assert progress.detail in output
    assert output.count("Missing source file\n") == 1
    assert "\x1b[?25h" in output
    assert output.endswith("after failure\n")


@pytest.mark.parametrize("mode", ["redirected", "disabled", "debug", "warning", "application"])
def test_plain_logging_has_no_live_output(terminal, monkeypatch, mode):
    monkeypatch.setattr(sys, "stdout", terminal)
    logger = get_logger("test")
    if mode == "redirected":
        monkeypatch.setattr(terminal, "isatty", lambda: False)
    if mode == "application":
        logging.getLogger("weavehr").addHandler(logging.StreamHandler(terminal))
    else:
        configure_logging(format="%(message)s", live_progress=mode != "disabled")
        if mode in ("debug", "warning"):
            set_log_level("DEBUG" if mode == "debug" else "WARNING")
    with StepProgress().display("extraction", perf_counter()):
        logger.warning("Missing source file")
    assert terminal.getvalue() == "Missing source file\n"

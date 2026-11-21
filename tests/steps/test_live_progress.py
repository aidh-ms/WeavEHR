"""Terminal and batch progress reporting, including end-to-end step counts."""

import io
import logging
import os
import sys
from collections.abc import Generator
from pathlib import Path
from threading import Event
from time import perf_counter

import pytest

from tests.steps.conftest import load_concept_config, load_extracation_config
from weavehr import ConceptStep, ExtractionStep, PersistenceStep, WeavEHRProject, dataset_config_registry
from weavehr.logging import _StepProgress, _shares_terminal, configure_logging, get_logger, set_log_level
from weavehr.steps.concept.registry import concept_config_registry


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


def test_live_progress_preserves_warning_and_restores_cursor_on_error(
    terminal: Terminal, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdout", terminal)
    configure_logging(format="%(message)s")
    progress = _StepProgress()
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
def test_plain_logging_has_no_live_output(terminal: Terminal, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
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
    with _StepProgress().display("extraction", perf_counter()):
        logger.warning("Missing source file")
    assert terminal.getvalue() == "Missing source file\n"


@pytest.mark.parametrize("shared", [True, False], ids=["same-terminal", "redirected-stderr"])
def test_stderr_keeps_its_destination(terminal: Terminal, monkeypatch: pytest.MonkeyPatch, shared: bool) -> None:
    stderr = terminal if shared else io.StringIO()
    monkeypatch.setattr(sys, "stdout", terminal)
    monkeypatch.setattr(sys, "stderr", stderr)
    configure_logging(format="%(message)s")
    with _StepProgress().display("extraction", perf_counter()):
        assert (sys.stderr is not stderr) == shared
        print("missing units", file=sys.stderr)
        sys.stderr.write("final diagnostic [/literal]")
    assert sys.stderr is stderr
    assert stderr.getvalue().count("missing units") == 1
    assert stderr.getvalue().count("final diagnostic [/literal]") == 1
    if not shared:
        assert "missing units" not in terminal.getvalue()


def test_stderr_may_be_a_second_descriptor_on_the_same_or_another_terminal() -> None:
    pty = pytest.importorskip("pty")
    master, slave = pty.openpty()
    other_master, other_slave = pty.openpty()
    with (
        os.fdopen(master, "rb"),
        os.fdopen(other_master, "rb"),
        os.fdopen(slave, "w") as stdout,
        os.fdopen(os.dup(slave), "w") as same,
        os.fdopen(other_slave, "w") as other,
    ):
        assert _shares_terminal(stdout, same)
        assert not _shares_terminal(stdout, other)


@pytest.mark.parametrize("mode", ["redirected", "application", "terminal-without-rich"])
def test_plain_logging_reports_a_long_operation(terminal: Terminal, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(sys, "stdout", terminal)
    if mode == "terminal-without-rich":
        monkeypatch.setitem(sys.modules, "rich", None)
        monkeypatch.setitem(sys.modules, "rich.console", None)
    else:
        monkeypatch.setattr(terminal, "isatty", lambda: False)
    monkeypatch.setattr("weavehr.logging._PROGRESS_INTERVAL", 0.01)
    package = logging.getLogger("weavehr")
    if mode == "application":
        package.addHandler(logging.StreamHandler(terminal))
        package.setLevel(logging.INFO)
    else:
        configure_logging(format="%(message)s")

    reported = Event()

    def observe(record: logging.LogRecord) -> bool:
        if "chartevents" in record.getMessage():
            reported.set()
        return True

    package.handlers[0].addFilter(observe)
    progress = _StepProgress()
    with progress.display("extraction", perf_counter()):
        progress.detail = "mimic-iv/2.2 | chartevents | 1/19 tables processed"
        assert reported.wait(5)
    assert progress.detail in terminal.getvalue()
    assert "\x1b" not in terminal.getvalue()


def test_steps_render_final_dataset_counts(
    terminal: Terminal, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, extraction_config: Path, concept_config: Path
) -> None:
    monkeypatch.setattr(sys, "stdout", terminal)
    configure_logging(format="%(message)s")
    config = tmp_path / "config"
    (config / "concepts" / "unmapped.yml").write_text("name: unmapped\nversion: 1.0.0\n")
    load_extracation_config(config / "testdb" / "1.0" / "tables")
    load_concept_config(config / "concepts", [config / "testdb" / "1.0" / "mappings"])
    project = WeavEHRProject(tmp_path / "project")
    PersistenceStep.load(project, extraction_config).run()
    ExtractionStep.load(project, extraction_config).run()
    ConceptStep.load(project, concept_config).run()
    mapped = sum(bool(c.get_dataset_concepts("testdb", "1.0")) for c in concept_config_registry.values())
    assert 0 < mapped < len(concept_config_registry)
    tables = len(dataset_config_registry.filter("testdb", "1.0"))
    files = len(list((project.workspace_path / "persistence" / "testdb" / "1.0").glob("*.parquet")))
    output = terminal.getvalue()
    assert f"testdb/1.0 | {files}/{files} sources processed" in output
    assert f"testdb/1.0 | {tables}/{tables} tables processed" in output
    assert f"testdb/1.0 | {mapped}/{mapped} concepts processed" in output

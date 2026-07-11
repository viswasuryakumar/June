"""Focused executable checks for the standalone auth health-check CLI."""

from __future__ import annotations

import importlib
from contextlib import contextmanager

from src.auth.context import BrowserContextConfig

auth_check = importlib.import_module("src.auth.check")


class _Page:
    def __init__(self) -> None:
        self.url = ""
        self.closed = False

    def goto(self, url: str) -> None:
        self.url = url

    def close(self) -> None:
        self.closed = True


class _Context:
    def __init__(self, page: _Page) -> None:
        self.page = page

    def new_page(self) -> _Page:
        return self.page


def test_check_login_state_reports_injected_health_result(tmp_path, monkeypatch) -> None:
    page = _Page()

    @contextmanager
    def fake_get_context(config):
        yield _Context(page)

    monkeypatch.setattr(auth_check, "get_context", fake_get_context)
    monkeypatch.setattr(auth_check.SelectorRegistry, "load", lambda path: object())
    monkeypatch.setattr("src.auth.context.is_logged_in", lambda *args, **kwargs: True)

    result = auth_check.check_login_state(
        BrowserContextConfig(profile_dir=tmp_path / "profile"),
        base_url="https://example.test",
        dashboard_path="/dashboard",
        registry_path=tmp_path / "selectors.yaml",
        run_id="review-run",
    )

    assert result.logged_in is True
    assert result.run_id == "review-run"
    assert page.url == "https://example.test/dashboard"
    assert page.closed is True


def test_main_returns_status_from_check_result(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(auth_check, "configure_logging", lambda run_id: _Logger())
    monkeypatch.setattr(
        auth_check,
        "check_login_state",
        lambda *args, **kwargs: auth_check.CheckResult(
            run_id="run", logged_in=False, profile_dir=str(tmp_path), base_url="test"
        ),
    )

    assert auth_check.main(["--profile-dir", str(tmp_path)]) == 1
    assert '"logged_in": false' in capsys.readouterr().out


def test_main_converts_operational_failure_to_exit_one(tmp_path, monkeypatch) -> None:
    """REV-005: a check that could not run (browser launch, selector-load,
    navigation failure, etc.) must still exit 1 per this module's own
    documented contract, not escape as a raw traceback.
    """
    monkeypatch.setattr(auth_check, "configure_logging", lambda run_id: _Logger())

    def fail(*args, **kwargs):
        raise RuntimeError("browser unavailable")

    monkeypatch.setattr(auth_check, "check_login_state", fail)
    assert auth_check.main(["--profile-dir", str(tmp_path)]) == 1


def test_main_operational_failure_prints_error_message(tmp_path, monkeypatch, capsys) -> None:
    """The structured failure JSON printed on an operational failure must
    include an `"error"` key carrying the exception message, so a human
    or downstream tooling reading the CLI's stdout can see what broke.
    """
    monkeypatch.setattr(auth_check, "configure_logging", lambda run_id: _Logger())

    def fail(*args, **kwargs):
        raise RuntimeError("browser unavailable")

    monkeypatch.setattr(auth_check, "check_login_state", fail)
    assert auth_check.main(["--profile-dir", str(tmp_path)]) == 1

    out = capsys.readouterr().out
    assert '"error"' in out
    assert "browser unavailable" in out


class _Logger:
    def info(self, *args, **kwargs) -> None:
        pass

    def log(self, *args, **kwargs) -> None:
        # log_event() (src/observability/logging.py) calls logger.log(level, ...)
        # directly rather than logger.info()/.warning(), so this fake needs a
        # matching no-op to stand in for a real logging.Logger.
        pass

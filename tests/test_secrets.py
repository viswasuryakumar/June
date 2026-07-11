import pytest
from src.config.secrets import load_secrets
from src.contracts.exceptions import SecretsError


def test_missing_required_secrets_fail_fast(monkeypatch):
    monkeypatch.delenv("JOBRIGHT_EMAIL", raising=False)
    monkeypatch.delenv("JOBRIGHT_PASSWORD", raising=False)
    with pytest.raises(SecretsError):
        load_secrets(env_file=None)


def test_secrets_load_from_env(monkeypatch):
    monkeypatch.setenv("JOBRIGHT_EMAIL", "user@example.com")
    monkeypatch.setenv("JOBRIGHT_PASSWORD", "hunter2")
    secrets = load_secrets(env_file=None)
    assert secrets.jobright_email == "user@example.com"
    assert secrets.jobright_password == "hunter2"


def test_secrets_repr_never_leaks_values(monkeypatch):
    monkeypatch.setenv("JOBRIGHT_EMAIL", "user@example.com")
    monkeypatch.setenv("JOBRIGHT_PASSWORD", "hunter2")
    secrets = load_secrets(env_file=None)
    assert "hunter2" not in repr(secrets)
    assert "user@example.com" not in repr(secrets)
    assert "hunter2" not in str(secrets)


def test_redaction_values_lists_all_secrets(monkeypatch):
    monkeypatch.setenv("JOBRIGHT_EMAIL", "user@example.com")
    monkeypatch.setenv("JOBRIGHT_PASSWORD", "hunter2")
    monkeypatch.setenv("NOTIFIER_SLACK_WEBHOOK_URL", "https://hooks.slack.com/abc")
    secrets = load_secrets(env_file=None)
    values = secrets.redaction_values()
    assert "hunter2" in values
    assert "user@example.com" in values
    assert "https://hooks.slack.com/abc" in values


def test_require_false_allows_missing_secrets(monkeypatch):
    monkeypatch.delenv("JOBRIGHT_EMAIL", raising=False)
    monkeypatch.delenv("JOBRIGHT_PASSWORD", raising=False)
    secrets = load_secrets(env_file=None, require=False)
    assert secrets.jobright_email == ""
    assert secrets.jobright_password == ""

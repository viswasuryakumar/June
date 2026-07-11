from pathlib import Path

import pytest
import yaml
from src.config.profile import load_profile
from src.config.settings import load_settings
from src.contracts.exceptions import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_load_shipped_settings_yaml():
    settings = load_settings(REPO_ROOT / "config" / "settings.yaml")
    assert settings.max_applications_per_day > 0
    assert 0 <= settings.min_match_score <= 100
    assert settings.approval_mode in {"auto", "approve_each", "approve_batch"}


def test_load_shipped_profile_yaml():
    profile = load_profile(REPO_ROOT / "config" / "profile.yaml")
    assert profile.email
    assert profile.work_authorized in {"yes", "no", "decline_to_answer"}


def test_settings_missing_file_raises_config_error(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "does-not-exist.yaml")


def test_settings_invalid_regex_raises(tmp_path: Path):
    bad = tmp_path / "settings.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "max_applications_per_day": 5,
                "min_match_score": 70,
                "title_include_regexes": ["("],  # invalid regex
            }
        )
    )
    with pytest.raises(ConfigError):
        load_settings(bad)


def test_settings_rejects_unknown_fields(tmp_path: Path):
    bad = tmp_path / "settings.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "max_applications_per_day": 5,
                "min_match_score": 70,
                "totally_made_up_field": True,
            }
        )
    )
    with pytest.raises(ConfigError):
        load_settings(bad)


def test_profile_missing_file_raises_config_error(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_profile(tmp_path / "does-not-exist.yaml")


def test_profile_minimal_valid_file(tmp_path: Path):
    minimal = tmp_path / "profile.yaml"
    minimal.write_text(yaml.safe_dump({"full_name": "Test User"}))
    profile = load_profile(minimal)
    assert profile.full_name == "Test User"
    assert profile.eeo_self_id.gender == "decline_to_answer"
    assert profile.learned_answers == []

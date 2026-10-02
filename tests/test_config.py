from pathlib import Path

import pytest

from alfred.config import AlfredConfig, ConfigError, load_config


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_when_no_config_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # an empty folder with no alfred.toml
    assert load_config() == AlfredConfig()


def test_loads_values_from_file(tmp_path):
    path = write(
        tmp_path / "alfred.toml",
        '[alfred]\nname = "Jeeves"\nlog_level = "debug"\nmodules = []\n',
    )
    config = load_config(path)
    assert config.name == "Jeeves"
    assert config.log_level == "DEBUG"  # normalised to upper case
    assert config.modules == ()


def test_missing_keys_fall_back_to_defaults(tmp_path):
    path = write(tmp_path / "alfred.toml", '[alfred]\nname = "Jeeves"\n')
    config = load_config(path)
    assert config.log_level == AlfredConfig().log_level
    assert config.modules == AlfredConfig().modules


def test_explicit_missing_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_invalid_toml_is_an_error(tmp_path):
    path = write(tmp_path / "alfred.toml", "[alfred\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path)


def test_invalid_log_level_is_an_error(tmp_path):
    path = write(tmp_path / "alfred.toml", '[alfred]\nlog_level = "LOUD"\n')
    with pytest.raises(ConfigError, match="log_level"):
        load_config(path)


def test_modules_must_be_a_list_of_strings(tmp_path):
    path = write(tmp_path / "alfred.toml", '[alfred]\nmodules = "hello"\n')
    with pytest.raises(ConfigError, match="modules"):
        load_config(path)

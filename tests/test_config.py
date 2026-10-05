from pathlib import Path

import pytest

from alfred.config import AlfredConfig, ConfigError, ObsidianSettings, load_config

NO_ENV: dict[str, str] = {}  # pass this so the real environment can't affect tests


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_when_no_config_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # an empty folder with no alfred.toml
    assert load_config(env=NO_ENV) == AlfredConfig()


def test_loads_values_from_file(tmp_path):
    path = write(
        tmp_path / "alfred.toml",
        '[alfred]\nname = "Jeeves"\nlog_level = "debug"\nmodules = []\n',
    )
    config = load_config(path, env=NO_ENV)
    assert config.name == "Jeeves"
    assert config.log_level == "DEBUG"  # normalised to upper case
    assert config.modules == ()


def test_missing_keys_fall_back_to_defaults(tmp_path):
    path = write(tmp_path / "alfred.toml", '[alfred]\nname = "Jeeves"\n')
    config = load_config(path, env=NO_ENV)
    assert config.log_level == AlfredConfig().log_level
    assert config.modules == AlfredConfig().modules
    assert config.obsidian == ObsidianSettings()


def test_explicit_missing_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml", env=NO_ENV)


def test_invalid_toml_is_an_error(tmp_path):
    path = write(tmp_path / "alfred.toml", "[alfred\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path, env=NO_ENV)


def test_invalid_log_level_is_an_error(tmp_path):
    path = write(tmp_path / "alfred.toml", '[alfred]\nlog_level = "LOUD"\n')
    with pytest.raises(ConfigError, match="log_level"):
        load_config(path, env=NO_ENV)


def test_modules_must_be_a_list_of_strings(tmp_path):
    path = write(tmp_path / "alfred.toml", '[alfred]\nmodules = "hello"\n')
    with pytest.raises(ConfigError, match="modules"):
        load_config(path, env=NO_ENV)


# --- [obsidian] section ------------------------------------------------------


def test_obsidian_settings_from_file(tmp_path):
    path = write(
        tmp_path / "alfred.toml",
        '[obsidian]\nurl = "http://localhost:27123/"\nca_cert = "ca.crt"\ntimeout = 3\n',
    )
    obsidian = load_config(path, env=NO_ENV).obsidian
    assert obsidian.url == "http://localhost:27123"  # trailing slash removed
    assert obsidian.ca_cert == tmp_path / "ca.crt"  # relative to the config file's folder
    assert obsidian.timeout == 3.0
    assert obsidian.api_key is None


def test_environment_overrides_file(tmp_path):
    path = write(tmp_path / "alfred.toml", '[obsidian]\nurl = "https://127.0.0.1:27124"\n')
    env = {
        "OBSIDIAN_URL": "http://127.0.0.1:27123",
        "OBSIDIAN_API_KEY": "secret-key",
        "OBSIDIAN_CA_CERT": "other.crt",
    }
    obsidian = load_config(path, env=env).obsidian
    assert obsidian.url == "http://127.0.0.1:27123"
    assert obsidian.api_key == "secret-key"
    assert obsidian.ca_cert == Path("other.crt")


def test_relative_paths_do_not_depend_on_the_current_folder(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    path = write(project / "alfred.toml", '[obsidian]\nca_cert = "obsidian.crt"\n')
    monkeypatch.chdir(tmp_path)  # e.g. Windows starting Alfred at login from another folder
    assert load_config(path, env=NO_ENV).obsidian.ca_cert == project / "obsidian.crt"


def test_absolute_ca_cert_stays_absolute(tmp_path):
    cert = tmp_path / "elsewhere" / "ca.crt"
    path = write(tmp_path / "alfred.toml", f"[obsidian]\nca_cert = '{cert}'\n")
    assert load_config(path, env=NO_ENV).obsidian.ca_cert == cert


def test_api_key_in_config_file_is_rejected(tmp_path):
    path = write(tmp_path / "alfred.toml", '[obsidian]\napi_key = "oops"\n')
    with pytest.raises(ConfigError, match="OBSIDIAN_API_KEY"):
        load_config(path, env=NO_ENV)


def test_bad_obsidian_url_is_an_error(tmp_path):
    path = write(tmp_path / "alfred.toml", '[obsidian]\nurl = "127.0.0.1:27124"\n')
    with pytest.raises(ConfigError, match="url"):
        load_config(path, env=NO_ENV)


def test_api_key_is_hidden_from_repr():
    settings = ObsidianSettings(api_key="super-secret")
    assert "super-secret" not in repr(settings)
    assert "super-secret" not in repr(AlfredConfig(obsidian=settings))


# --- [copilot] and [ui] sections ---------------------------------------------------


def test_copilot_and_ui_defaults(tmp_path):
    config = load_config(write(tmp_path / "alfred.toml", ""), env=NO_ENV)
    assert config.copilot.model is None
    assert config.copilot.timeout == 120.0
    assert config.ui.hotkey == "ctrl+alt+space"


def test_copilot_and_ui_from_file(tmp_path):
    path = write(
        tmp_path / "alfred.toml",
        '[copilot]\nmodel = "gpt-5"\ntimeout = 30\n[ui]\nhotkey = "Alt+M"\n',
    )
    config = load_config(path, env=NO_ENV)
    assert config.copilot.model == "gpt-5"
    assert config.copilot.timeout == 30.0
    assert config.ui.hotkey == "alt+m"


@pytest.mark.parametrize("key", ["token", "github_token"])
def test_github_token_in_config_file_is_rejected(tmp_path, key):
    path = write(tmp_path / "alfred.toml", f'[copilot]\n{key} = "github_pat_oops"\n')
    with pytest.raises(ConfigError, match="COPILOT_GITHUB_TOKEN"):
        load_config(path, env=NO_ENV)


def test_bad_copilot_timeout(tmp_path):
    path = write(tmp_path / "alfred.toml", "[copilot]\ntimeout = 0\n")
    with pytest.raises(ConfigError, match="timeout"):
        load_config(path, env=NO_ENV)


def test_obsidian_launch_settings(tmp_path):
    defaults = load_config(write(tmp_path / "alfred.toml", ""), env=NO_ENV).obsidian
    assert defaults.auto_launch is True
    assert defaults.vault is None
    assert defaults.launch_timeout == 30.0

    path = write(tmp_path / "custom.toml", '[obsidian]\nauto_launch = false\nvault = "alfred"\nlaunch_timeout = 15\n')
    custom = load_config(path, env=NO_ENV).obsidian
    assert (custom.auto_launch, custom.vault, custom.launch_timeout) == (False, "alfred", 15.0)


@pytest.mark.parametrize("line", ['auto_launch = "yes"', "vault = 3", "launch_timeout = -1"])
def test_bad_obsidian_launch_settings(tmp_path, line):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path / "alfred.toml", f"[obsidian]\n{line}\n"), env=NO_ENV)

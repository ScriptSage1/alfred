from alfred.__main__ import main


def test_main_with_defaults(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no alfred.toml here, so defaults are used
    assert main([]) == 0
    assert "Hello, world!" in capsys.readouterr().out


def test_main_with_config_file(tmp_path, capsys):
    path = tmp_path / "custom.toml"
    path.write_text("[alfred]\nmodules = []\n", encoding="utf-8")

    assert main(["--config", str(path)]) == 0
    assert "Hello" not in capsys.readouterr().out  # hello was not loaded


def test_main_missing_config_returns_2(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "nope.toml")]) == 2
    assert "not found" in capsys.readouterr().err


def test_main_unknown_module_returns_1(tmp_path, capsys):
    path = tmp_path / "custom.toml"
    path.write_text('[alfred]\nmodules = ["nope"]\n', encoding="utf-8")

    assert main(["--config", str(path)]) == 1
    assert "no Alfred module named 'nope'" in capsys.readouterr().err

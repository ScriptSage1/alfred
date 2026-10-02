import pytest

from alfred.app import Alfred, ModuleLoadError, load_module
from alfred.config import AlfredConfig
from alfred.modules.hello import HelloModule


def test_load_hello_module():
    module = load_module("hello")
    assert isinstance(module, HelloModule)
    assert module.name == "hello"


def test_hello_greet():
    assert HelloModule().greet("Bruce") == "Hello, Bruce! Alfred is at your service."


def test_unknown_module_is_an_error():
    with pytest.raises(ModuleLoadError, match="no Alfred module named 'nope'"):
        load_module("nope")


def test_module_without_factory_is_an_error():
    # base.py is a real file in alfred.modules but has no create_module().
    with pytest.raises(ModuleLoadError, match="create_module"):
        load_module("base")


def test_invalid_module_name_is_an_error():
    with pytest.raises(ModuleLoadError, match="invalid module name"):
        load_module("../hello")


def test_run_loads_and_starts_configured_modules(capsys):
    app = Alfred(AlfredConfig(modules=("hello",)))
    app.run()

    assert list(app.modules) == ["hello"]
    assert "Hello, world!" in capsys.readouterr().out


def test_run_with_no_modules():
    app = Alfred(AlfredConfig(modules=()))
    app.run()
    assert app.modules == {}

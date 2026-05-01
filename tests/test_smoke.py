import importlib.util
from pathlib import Path


def load_fmanager_module():
    path = Path(__file__).resolve().parents[1] / "fmanager.py"
    spec = importlib.util.spec_from_file_location("fmanager", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_module_loads():
    module = load_fmanager_module()
    assert hasattr(module, "main")
    assert hasattr(module, "load_config")


def test_next_free_port_returns_int():
    module = load_fmanager_module()
    port = module.next_free_port(8999)
    assert isinstance(port, int)
    assert port >= 8999

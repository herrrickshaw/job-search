"""Load job_search / iimjobs against a sandbox HOME (they bind ~/... paths at import time)."""
import importlib.util
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "Downloads").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    return h


@pytest.fixture
def js(home):
    """A freshly imported job_search module bound to the sandbox HOME."""
    spec = importlib.util.spec_from_file_location("job_search_under_test", REPO / "job_search.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop(spec.name, None)


@pytest.fixture
def no_sleep(monkeypatch):
    import time
    s = []
    monkeypatch.setattr(time, "sleep", lambda x: s.append(x))
    return s

"""iimjobs.py listing parser on a synthetic Jina-Reader markdown page."""
import importlib
import sys
import urllib.request

import pytest


@pytest.fixture
def iim(monkeypatch, tmp_path):
    """Import with the network and HOME sandboxed; record any fetch attempt."""
    calls = []

    def no_net(*a, **k):
        calls.append(a)
        raise OSError("network disabled in tests")
    monkeypatch.setattr(urllib.request, "urlopen", no_net)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sys.modules.pop("iimjobs", None)
    mod = importlib.import_module("iimjobs")
    mod._import_calls = list(calls)
    return mod


def test_import_has_no_side_effects(iim, tmp_path):
    """Importing the module ran main(): it scraped every category page and
    wrote iimjobs_results.json into the current directory."""
    assert iim._import_calls == []
    assert not (tmp_path / "iimjobs_results.json").exists()


MD = (
    "Jump to content\n"
    "[![logo](https://x/logo.png) Acme Energy - Manager, LNG Strategy 8 - 12 yrs Mumbai, Delhi "
    "Posted 3 days ago LNG Strategy](https://www.iimjobs.com/j/acme-manager-lng-1234?ref=k)\n"
    "[Short](https://www.iimjobs.com/j/x-1)\n"
    "[Beta Infra - Analyst, PPP advisory no experience band listed here]"
    "(https://www.iimjobs.com/j/beta-analyst-99)\n"
    "[Not a job](https://www.iimjobs.com/k/consulting-jobs)\n"
)


def test_parse_extracts_title_years_location_posted(iim):
    rows = iim.parse(MD)
    assert len(rows) == 2
    a = rows[0]
    assert a["url"] == "https://www.iimjobs.com/j/acme-manager-lng-1234"
    assert a["title"] == "Acme Energy - Manager, LNG Strategy"
    assert a["yrs"] == (8, 12)
    assert a["location"] == "Mumbai, Delhi"
    assert a["posted"] == "3 days ago"
    b = rows[1]
    assert b["yrs"] is None and b["posted"] is None and b["location"] == ""
    assert b["title"].startswith("Beta Infra - Analyst")


def test_parse_empty(iim):
    assert iim.parse("") == []

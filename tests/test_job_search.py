"""job_search.py pure helpers with synthetic postings and a synthetic CV."""
import json
import subprocess
import types

import pytest

import job_search as js

CV = "process engineer with lng and natural gas business development, financial modelling, excel, python"


def test_score_coverage_hand_computed():
    post = "Role: LNG business development; financial modelling and Power BI required."
    s = js.score(post, CV)
    # present vocab: lng, business development, financial modelling, power bi -> 3 of 4 in CV
    assert s["n"] == 4 and s["cov"] == 75 and s["miss"] == ["power bi"]


def test_score_none_without_vocab_and_handles_none():
    assert js.score("Barista wanted, latte art", CV) is None
    assert js.score(None, CV) is None


def test_score_punctuation_normalised():
    assert js.score("LNG/natural-gas (FDI)", "lng fdi")["n"] >= 2


@pytest.mark.parametrize("post", [
    "Excellent communication skills required",   # 'excel'
    "Work from home; Windows laptop provided",     # 'wind'
    "An irregular schedule",                       # 'irr'
])
def test_score_does_not_match_vocab_inside_other_words(post):
    """Substring matching counted 'excel' in 'excellent', 'wind' in 'windows',
    'irr' in 'irregular' -- inflating both n and coverage for unrelated posts."""
    assert js.score(post, CV + " wind irr") is None


def test_score_keeps_simple_plurals():
    s = js.score("Renewables and investors", "renewable investor")
    assert s is not None and s["cov"] == 100


@pytest.mark.parametrize("title,junk", [
    ("Summer Intern - Energy", True),
    ("Graduate Trainee", True),
    ("Senior Software Engineer", True),
    ("International Business Manager", False),
    ("Internal Audit Lead", False),
    ("Manager, LNG Origination", False),
    ("Business Developer - LNG", False),
])
def test_junk_title_filter(title, junk):
    assert bool(js.JUNK.search(title)) is junk


def test_norm_url_drops_scheme_www_trailing_slash_and_tracking():
    assert js.norm_url("https://www.Example.com/Jobs/1/?utm_source=x") == "example.com/jobs/1"
    assert js.norm_url("http://example.com/jobs/1") == "example.com/jobs/1"
    assert js.norm_url(None) == ""


@pytest.mark.xfail(strict=True, reason="known: norm_url drops the whole query string, so job "
                   "boards that identify postings by ?id= collapse to one dedupe key; fixing it "
                   "changes seen.json keys and Adzuna's per-run tracking params, needs a decision")
def test_norm_url_keeps_identifying_query_params():
    assert js.norm_url("https://careers.example.com/job?id=1") != \
        js.norm_url("https://careers.example.com/job?id=2")


def test_adzuna_creds_from_env_and_store(monkeypatch, tmp_path):
    store = tmp_path / "creds.env"
    monkeypatch.setattr(js, "STORE", store)
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    assert js.adzuna_creds() == (None, None)
    store.write_text('OTHER=1\nADZUNA_APP_ID="abc"\nADZUNA_APP_KEY=def \n')
    assert js.adzuna_creds() == ("abc", "def")
    monkeypatch.setenv("ADZUNA_APP_ID", "e1")
    monkeypatch.setenv("ADZUNA_APP_KEY", "e2")
    assert js.adzuna_creds() == ("e1", "e2")


def test_exa_search_parses_chunks_without_network(monkeypatch):
    text = ("Title: LNG Manager\nURL: https://jobs.example.com/1\nPublished: 2026-10-20T00:00\n"
            "Author: N/A\nbody\n---\nTitle: no url here\n---\n"
            "Title: Gas Analyst\nURL: https://jobs.example.com/2\nAuthor: Acme Energy\n")
    out = json.dumps({"content": [{"type": "text", "text": text}]})
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")
    monkeypatch.setattr(js.subprocess, "run", fake_run)
    monkeypatch.setattr(js.time, "sleep", lambda s: None)
    rows = js.exa_search(["q1"], 5, verbose=False)
    assert calls[0][:3] == ["mcporter", "call", "exa.web_search_exa"]
    assert [(r["title"], r["company"], r["posted"]) for r in rows] == [
        ("LNG Manager", None, "2026-10-20"), ("Gas Analyst", "Acme Energy", None)]


def test_exa_search_tolerates_tool_failure(monkeypatch):
    monkeypatch.setattr(js.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    monkeypatch.setattr(js.time, "sleep", lambda s: None)
    assert js.exa_search(["q"], 5, verbose=False) == []

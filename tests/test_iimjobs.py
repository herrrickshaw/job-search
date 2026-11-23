"""iimjobs.py runs its whole pipeline at import time, so every test executes it in a sandbox:
HOME -> tmp (for the CV), cwd -> tmp (results_iimjobs.json is written to the cwd), jina faked."""
import io
import json
import pathlib
import sys
import time
import urllib.request

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def listing(label, url, extra=""):
    return f"[{label}]({url}{extra})"


JOB1 = listing("![logo](http://img/x.png) Acme Energy - Head of LNG Strategy 8 - 12 yrs Mumbai Posted 2 days ago LNG Solar",
               "https://www.iimjobs.com/j/head-of-lng-strategy-1234", ' "title"')
JOB2 = listing("Beta Co - Junior Analyst 1 - 3 yrs Delhi Posted today Excel", "https://www.iimjobs.com/j/junior-analyst-1")
JOB3 = listing("Gamma - Policy Advisor 10 - 15 yrs Chennai . Posted yesterday Policy Government", "https://www.iimjobs.com/j/policy-advisor-3")
NOYRS = listing("Delta Industries Senior Manager Strategy Consulting Pune", "https://www.iimjobs.com/j/no-yrs-4")
SHORT = listing("short", "https://www.iimjobs.com/j/too-short-5")


def run(tmp_path, monkeypatch, pages, cv=None):
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True, exist_ok=True)
    if cv is not None:
        (home / "Downloads/CV_Umashankar_Master_2026-08-26.md").write_text(cv)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(work)
    sleeps, urls = [], []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

    class R:
        def __init__(self, t):
            self.t = t

        def read(self):
            return self.t.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout):
        urls.append((req.full_url, req.get_header("User-agent"), timeout))
        v = pages(req.full_url) if callable(pages) else pages
        if isinstance(v, Exception):
            raise v
        return R(v)

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    real = REPO / "iimjobs.py"
    ns = {"__name__": "iimjobs_under_test", "__file__": str(real)}
    exec(compile(real.read_text(encoding="utf-8"), str(real), "exec"), ns)
    ns["_sleeps"], ns["_urls"] = sleeps, urls
    return ns, work


def results(work):
    return json.loads((work / "results_iimjobs.json").read_text())


def test_parse_extracts_fields(tmp_path, monkeypatch):
    ns, _ = run(tmp_path, monkeypatch, "")
    parse = ns["parse"]
    rows = parse("\n".join(["noise", JOB1, JOB2, JOB3, NOYRS, SHORT, "iimjobs.com/j/ but no parenthesised url here"]))
    assert [r["url"] for r in rows] == ["https://www.iimjobs.com/j/head-of-lng-strategy-1234",
                                         "https://www.iimjobs.com/j/junior-analyst-1",
                                         "https://www.iimjobs.com/j/policy-advisor-3",
                                         "https://www.iimjobs.com/j/no-yrs-4"]       # < 15 chars of label text dropped
    r1, r2, r3, r4 = rows
    assert r1["title"] == "Acme Energy - Head of LNG Strategy" and r1["yrs"] == (8, 12) and r1["posted"] == "2 days ago"
    assert r1["location"] == "Mumbai" and "logo" not in r1["tags"] and "LNG Solar" in r1["tags"]
    assert r2["posted"] == "today" and r3["posted"] == "yesterday" and r3["location"] == "Chennai"      # " .\\u00b7" stripped
    assert r4["yrs"] is None and r4["posted"] is None and r4["location"] == "" and r4["title"] == r4["tags"][:120]


def test_parse_cuts_title_150_and_location_60(tmp_path, monkeypatch):
    ns, _ = run(tmp_path, monkeypatch, "")
    long_title = "T" * 200
    job = listing(f"{long_title} 5 - 9 yrs {'L' * 100} Posted 3 weeks ago", "https://www.iimjobs.com/j/long-1")
    r = ns["parse"](job)[0]
    assert len(r["title"]) == 150 and len(r["location"]) == 60
    assert r["posted"] == "3 weeks ago"
    big = listing("X" * 300, "https://www.iimjobs.com/j/nolimit-2")
    assert len(ns["parse"](big)[0]["title"]) == 120                      # no 'yrs' block: title is the first 120 chars of the tags


def test_parse_ignores_lines_without_job_urls(tmp_path, monkeypatch):
    ns, _ = run(tmp_path, monkeypatch, "")
    assert ns["parse"]("[x](https://www.iimjobs.com/k/other)\nplain text\n") == []


def test_jina_success_and_retry(tmp_path, monkeypatch):
    ns, _ = run(tmp_path, monkeypatch, "")
    calls = []
    seq = iter([OSError("a"), b"caf\xc3\xa9 \xff ok"])

    class R:
        def __init__(self, t):
            self.t = t

        def read(self):
            return self.t

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout):
        calls.append((req.full_url, req.get_header("User-agent"), timeout))
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return R(v)

    sl = []
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setattr(time, "sleep", lambda s: sl.append(s))
    assert ns["jina"]("https://x/y") == "caf\u00e9  ok"                 # undecodable bytes are dropped, not fatal
    assert calls[0] == ("https://r.jina.ai/https://x/y", "Mozilla/5.0", 120) and sl == [3] and len(calls) == 2


def test_jina_gives_up_and_logs_once(tmp_path, monkeypatch, capsys):
    ns, _ = run(tmp_path, monkeypatch, "")
    n = []

    def boom(req, timeout):
        n.append(1)
        raise OSError("down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    capsys.readouterr()
    assert ns["jina"]("https://x") == "" and len(n) == 2
    assert capsys.readouterr().err.count("! https://x: down") == 1
    n.clear()
    assert ns["jina"]("https://x", tries=3) == "" and len(n) == 3


def test_full_run_scores_dedupes_and_orders(tmp_path, monkeypatch, capsys):
    page = "\n".join([JOB1, JOB2, JOB3, NOYRS])
    ns, work = run(tmp_path, monkeypatch, page, cv="LNG and Solar and Policy and Government experience")
    rows = results(work)
    assert len(rows) == 4                                              # the same four URLs on 9 pages are de-duplicated
    assert [r["cat"] for r in rows] == ["consulting-energy-sector"] * 4
    by = {r["url"].rsplit("/", 1)[1]: r for r in rows}
    j1, j3 = by["head-of-lng-strategy-1234"], by["policy-advisor-3"]
    assert j1["fit"] is True and j3["fit"] is True and by["junior-analyst-1"]["fit"] is False and by["no-yrs-4"]["fit"] is False
    assert (j1["cov"], j1["n"]) == (50, 4) and (j3["cov"], j3["n"]) == (100, 2)
    order = [r["url"].rsplit("/", 1)[1] for r in rows]
    assert order[:2] == ["policy-advisor-3", "head-of-lng-strategy-1234"]          # fit first; 100*sqrt(2) beats 50*sqrt(4)
    assert set(order[2:]) == {"junior-analyst-1", "no-yrs-4"}
    out = capsys.readouterr()
    assert "4 unique listings | 2 in your ~10-yr band" in out.out
    assert " 50%  4kw 8-12y   2 days ago     Acme Energy - Head of LNG Strategy" in out.out
    assert "100%  2kw 10-15y  yesterday      Gamma - Policy Advisor" in out.out
    assert "Mumbai  ·  https://www.iimjobs.com/j/head-of-lng-strategy-1234" in out.out
    assert "consulting-energy-sector" in out.err and "  4 listings" in out.err
    assert len(ns["_urls"]) == 9 and ns["_urls"][0][0] == "https://r.jina.ai/https://www.iimjobs.com/k/consulting-energy-sector-jobs"
    assert ns["_sleeps"].count(1.5) == 9


@pytest.mark.parametrize("lo,hi,fit", [(8, 12, True), (12, 14, True), (6, 8, True), (13, 20, False), (1, 7, False), (0, 100, True)])
def test_fit_band_boundaries(tmp_path, monkeypatch, lo, hi, fit):
    job = listing(f"Acme - Role Manager {lo} - {hi} yrs Pune Posted today LNG", f"https://www.iimjobs.com/j/role-{lo}-{hi}")
    ns, work = run(tmp_path, monkeypatch, job, cv="lng")
    assert results(work)[0]["fit"] is fit


def test_missing_cv_gives_zero_coverage(tmp_path, monkeypatch):
    ns, work = run(tmp_path, monkeypatch, JOB1)
    r = results(work)[0]
    assert r["cov"] == 0 and r["n"] == 4


def test_no_vocab_gives_zero_n(tmp_path, monkeypatch):
    job = listing("Acme - Barista wanted for cafe 8 - 12 yrs Pune Posted today", "https://www.iimjobs.com/j/cafe-1")
    ns, work = run(tmp_path, monkeypatch, job, cv="lng")
    r = results(work)[0]
    assert r["n"] == 0 and r["cov"] == 0


def test_sort_by_coverage_times_sqrt_keywords(tmp_path, monkeypatch):
    a = listing("Aaa - Role A 8 - 12 yrs Pune Posted today lng", "https://www.iimjobs.com/j/aaa-1")                  # 1kw, 100% -> 100
    b = listing("Bbb - Role B 8 - 12 yrs Pune Posted today lng solar wind oil", "https://www.iimjobs.com/j/bbb-2")    # 4kw, 50% -> 100
    c = listing("Ccc - Role C 8 - 12 yrs Pune Posted today lng solar wind oil gas", "https://www.iimjobs.com/j/ccc-3")  # 5kw, 60% -> ~134
    ns, work = run(tmp_path, monkeypatch, "\n".join([a, b, c]), cv="lng solar gas")
    assert [r["url"].rsplit("/", 1)[1] for r in results(work)][0] == "ccc-3"


def test_failed_fetch_yields_empty_results(tmp_path, monkeypatch, capsys):
    ns, work = run(tmp_path, monkeypatch, OSError("net"))
    assert results(work) == []
    assert "0 unique listings | 0 in your ~10-yr band" in capsys.readouterr().out


def test_report_shows_at_most_28_fit_listings(tmp_path, monkeypatch, capsys):
    jobs = [listing(f"Co{i} - Role {i} Manager 8 - 12 yrs Pune Posted today lng", f"https://www.iimjobs.com/j/role-{i:02d}") for i in range(40)]
    ns, work = run(tmp_path, monkeypatch, "\n".join(jobs), cv="lng")
    out = capsys.readouterr().out
    assert "40 unique listings | 40 in your ~10-yr band" in out
    assert out.count("  ·  https://") == 28

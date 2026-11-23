import json
import subprocess
import sys
import types

import pytest


def proc(stdout="", returncode=0, stderr=""):
    return types.SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


# ---------------- scoring ----------------
def test_score_counts_vocab_and_misses(js):
    cv = "i know lng and python and sql"
    s = js.score("Looking for LNG, Python, SQL and hydrogen experience", cv)
    assert s == {"cov": 75, "n": 4, "miss": ["hydrogen"]}


def test_score_none_when_no_vocab_or_empty(js):
    assert js.score("completely unrelated words", "lng") is None
    assert js.score(None, "lng") is None and js.score("", "lng") is None


def test_score_rounding_and_miss_cap(js):
    text = " ".join(js.VOCAB[:13])
    s = js.score(text, "")
    assert s["cov"] == 0 and s["n"] == 13 and len(s["miss"]) == 10         # miss list is capped at 10
    s2 = js.score("lng solar wind", "lng")                                 # 1/3 -> 33
    assert s2["cov"] == 33
    s3 = js.score("lng solar", "lng solar x")
    assert s3["cov"] == 100


def test_score_punctuation_is_stripped_and_case_folded(js):
    assert js.score("LNG/Natural-Gas, (Python)!", "lng python")["n"] >= 2


def test_norm_url(js):
    assert js.norm_url("https://www.Example.com/Jobs/1/?utm=x") == "example.com/jobs/1"
    assert js.norm_url("http://example.com/a/") == "example.com/a"
    assert js.norm_url(None) == "" and js.norm_url("") == ""
    assert js.norm_url("https://a.com/x?y=1?z") == "a.com/x"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="job_search.py norm_url: the scheme/www prefix is stripped with a case-sensitive regex "
                          "BEFORE lower-casing, so 'HTTPS://WWW.Example.com/x' keeps its prefix and is not de-duplicated "
                          "against 'https://example.com/x'")
def test_norm_url_uppercase_scheme_is_normalised(js):
    assert js.norm_url("HTTPS://WWW.Example.com/Jobs/1/") == js.norm_url("https://example.com/jobs/1")


@pytest.mark.parametrize("title,junk", [
    ("Summer Internship", True), ("Intern", True), ("International Business Manager", False),
    ("Fresher program", True), ("Software Engineer II", True), ("Full-Stack dev", True), ("Fullstack role", True),
    ("Head of LNG", False), ("Backend Developer", True), ("Werkstudent Energie", True), ("Senior Nurse", True), ("DevOps lead", True),
])
def test_junk_regex(js, title, junk):
    assert bool(js.JUNK.search(title)) is junk


# ---------------- CV ----------------
def test_load_cv_missing(js, capsys):
    assert js.load_cv() == ""
    assert "CV not found" in capsys.readouterr().err


def test_load_cv_normalises(js, home):
    (home / "Downloads/CV_Umashankar_Master_2026-08-26.md").write_text("LNG   and\n\nPython\t Skills")
    assert js.load_cv() == "lng and python skills"


# ---------------- exa ----------------
EXA_TEXT = ("Title: Gas Manager\nURL: http://a.com/1\nPublished: 2026-02-03T10:00:00Z\nAuthor: Acme\n"
            "body about lng\n---\n"
            "Title: No Meta\nURL: http://a.com/2\nPublished: N/A\nAuthor: N/A\n"
            "\n----\n"
            "Title: Missing url only\n"
            "\n---\n"
            "URL: http://a.com/4\nbody without title\n")


def exa_payload(text):
    return json.dumps({"content": [{"type": "text", "text": text}, {"type": "image", "text": "ignored"}]})


def test_exa_search_parses_chunks(js, monkeypatch, no_sleep, capsys):
    calls = []

    def run(cmd, capture_output, text, timeout):
        calls.append((cmd, timeout))
        return proc(exa_payload(EXA_TEXT))

    monkeypatch.setattr(js.subprocess, "run", run)
    rows = js.exa_search(["q1"], 5)
    assert [r["title"] for r in rows] == ["Gas Manager", "No Meta"]
    r0, r1 = rows
    assert r0 == {"src": "exa", "title": "Gas Manager", "company": "Acme", "location": None, "posted": "2026-02-03",
                  "url": "http://a.com/1", "text": r0["text"]} and "body about lng" in r0["text"]
    assert r1["company"] is None and r1["posted"] is None
    assert calls[0][0] == ["mcporter", "call", "exa.web_search_exa", "query=q1", "numResults=5", "--output", "json"]
    assert calls[0][1] == 180 and no_sleep == [0.5]
    assert "exa: q1" in capsys.readouterr().err


def test_exa_search_quiet_and_failures(js, monkeypatch, no_sleep, capsys):
    seq = iter([proc(returncode=1, stderr="boom " * 50), proc("not json"), proc(exa_payload(EXA_TEXT))])
    monkeypatch.setattr(js.subprocess, "run", lambda *a, **k: next(seq))
    rows = js.exa_search(["a", "b", "c"], 3, verbose=False)
    assert len(rows) == 2
    err = capsys.readouterr().err
    assert "! exa: boom" in err and len(err.split("! exa: ")[1].split("\n")[0]) <= 120
    assert "! exa 'b'" in err and "cumulative" not in err
    assert no_sleep == [0.5]            # failed queries `continue` before the throttle


def test_exa_search_no_content_blocks(js, monkeypatch, no_sleep):
    monkeypatch.setattr(js.subprocess, "run", lambda *a, **k: proc("{}"))
    assert js.exa_search(["q"], 1) == []


# ---------------- adzuna ----------------
def test_adzuna_creds_from_env_and_file(js, home, monkeypatch):
    assert js.adzuna_creds() == (None, None)
    js.STORE.parent.mkdir(parents=True)
    js.STORE.write_text('OTHER=1\nADZUNA_APP_ID="abc"\nADZUNA_APP_KEY=def \n')
    assert js.adzuna_creds() == ("abc", "def")
    monkeypatch.setenv("ADZUNA_APP_ID", "envid")
    assert js.adzuna_creds() == ("abc", "def")                              # both env vars needed, else the file is used
    monkeypatch.setenv("ADZUNA_APP_KEY", "envkey")
    assert js.adzuna_creds() == ("envid", "envkey")


def test_adzuna_creds_partial_file(js, home):
    js.STORE.parent.mkdir(parents=True)
    js.STORE.write_text("ADZUNA_APP_ID=only\n")
    assert js.adzuna_creds() == (None, None)


class Resp:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_adzuna_search_maps_fields(js, monkeypatch, no_sleep, capsys):
    urls = []
    result = {"results": [{"title": "Analyst", "company": {"display_name": "Co"}, "location": {"display_name": "Pune"},
                           "created": "2026-03-04T00:00:00Z", "redirect_url": "http://x/1", "salary_min": 10, "salary_max": 20,
                           "description": "lng"},
                          {"title": "Bare"}]}

    def fake_open(url, timeout):
        urls.append((url, timeout))
        return Resp(json.dumps(result).encode())

    monkeypatch.setattr(js.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(js, "AD_QUERIES", ["q one"])
    rows = js.adzuna_search(["in", "zz"], 7, "ID", "KEY")
    assert len(rows) == 4
    r = rows[0]
    assert r == {"src": "adzuna", "title": "Analyst", "company": "Co", "location": "Pune", "posted": "2026-03-04",
                 "url": "http://x/1", "salary_min": 10, "salary_max": 20, "text": "lng", "country": "India"}
    assert rows[1]["company"] is None and rows[1]["location"] is None and rows[1]["posted"] == "" and rows[1]["url"] is None
    assert rows[2]["country"] == "zz"                                      # unknown country code is kept as is
    u, t = urls[0]
    assert u.startswith("https://api.adzuna.com/v1/api/jobs/in/search/1?") and t == 30
    assert "what=q+one" in u and "max_days_old=7" in u and "results_per_page=50" in u and "sort_by=date" in u
    assert "app_id=ID" in u and "app_key=KEY" in u
    assert no_sleep == [0.4, 0.4] and "adzuna in: 2 cumulative" in capsys.readouterr().err


def test_adzuna_search_failure_is_skipped(js, monkeypatch, no_sleep, capsys):
    def boom(url, timeout):
        raise OSError("net")

    monkeypatch.setattr(js.urllib.request, "urlopen", boom)
    monkeypatch.setattr(js, "AD_QUERIES", ["q"])
    assert js.adzuna_search(["in"], 3, "i", "k", verbose=False) == []
    err = capsys.readouterr().err
    assert "! adzuna in: net" in err and "cumulative" not in err and no_sleep == []


def test_adzuna_missing_results_key(js, monkeypatch, no_sleep):
    monkeypatch.setattr(js.urllib.request, "urlopen", lambda url, timeout: Resp(b"{}"))
    monkeypatch.setattr(js, "AD_QUERIES", ["q"])
    assert js.adzuna_search(["in"], 3, "i", "k") == []


# ---------------- linkedin ----------------
def test_mcp_builds_command_and_raises(js, monkeypatch):
    seen = []

    def run(cmd, capture_output, text, timeout):
        seen.append((cmd, timeout))
        return proc('{"ok": 1}')

    monkeypatch.setattr(js.subprocess, "run", run)
    assert js._mcp("search_jobs", keywords="lng", location=None, max_pages=1) == {"ok": 1}
    assert seen[0] == (["mcporter", "call", "linkedin.search_jobs", "keywords=lng", "max_pages=1", "--output", "json"], 600)
    monkeypatch.setattr(js.subprocess, "run", lambda *a, **k: proc(returncode=2, stderr="e" * 300))
    with pytest.raises(RuntimeError) as e:
        js._mcp("x")
    assert str(e.value) == "e" * 160


def li_stub(js, monkeypatch, search, details):
    calls = []

    def fake(tool, **kw):
        calls.append((tool, kw))
        if tool == "search_jobs":
            v = search[kw["keywords"]]
        else:
            v = details[kw["job_id"]]
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(js, "_mcp", fake)
    return calls


JOB = "Acme Corp\n\nHead of LNG Origination\n\nSingapore · 3 days ago · Over 100 applicants\n\nbody"


def test_linkedin_search_flow(js, monkeypatch, no_sleep, capsys):
    search = {"k1": {"sections": {"search_results": "Jump to x\nTitle A\nJobs you may like\nTitle B"}, "job_ids": ["1", "2"]},
              "lng k2": {"job_ids": ["2", "3"]}, "k3": RuntimeError("blocked")}
    details = {"1": {"sections": {"job_posting": JOB}, "url": "https://li/1"},
               "2": {"sections": {"job_posting": "Co\n\nSoftware Cook\n\nPune · 1 week ago"}},
               "3": {"sections": {"other": "free text", "num": 5}}}
    calls = li_stub(js, monkeypatch, search, details)
    rows = js.linkedin_search([("k1", "India"), ("lng k2", "Germany"), ("k3", "UK")], "past_week", 5)
    # ids 1,2,3 gathered (2 de-duplicated); job 2's title fails the domain filter
    assert [r["url"] for r in rows] == ["https://li/1", "https://www.linkedin.com/jobs/view/3"]
    r = rows[0]
    assert r["title"] == "Head of LNG Origination" and r["company"] == "Acme Corp" and r["location"] == "Singapore"
    assert r["posted"] == "3 days ago" and r["src"] == "linkedin" and r["text"] == JOB
    # job 3 has no job_posting section -> blob is the joined string sections; the hint keyword is the title
    assert rows[1]["title"] == "lng k2" and rows[1]["location"] == "Germany" and rows[1]["company"] == "free text"
    first = calls[0]
    assert first == ("search_jobs", {"keywords": "k1", "location": "India", "date_posted": "past_week",
                                     "experience_level": "mid_senior,director", "max_pages": 1, "sort_by": "date"})
    assert [c[0] for c in calls].count("get_job_details") == 3
    err = capsys.readouterr().err
    assert "! linkedin 'k3': blocked" in err and "3 detail calls spent" in err
    assert no_sleep.count(2.5) == 2 and no_sleep.count(2.0) == 3


def test_linkedin_detail_budget_and_errors(js, monkeypatch, no_sleep, capsys):
    search = {"k": {"job_ids": ["1", "2", "3", "4"]}}
    good = {"sections": {"job_posting": "Co\n\nGas Lead\n\nPune · 2 hours ago"}}
    details = {"1": RuntimeError("detail fail"), "2": good, "3": good, "4": good}
    calls = li_stub(js, monkeypatch, search, details)
    rows = js.linkedin_search([("k", "India")], "past_week", 2)
    # the failed call does not consume budget; budget 2 allows ids 2 and 3
    assert [c[1]["job_id"] for c in calls if c[0] == "get_job_details"] == ["1", "2", "3"]
    assert len(rows) == 2 and rows[0]["posted"] == "2 hours ago"
    assert "! detail 1: detail fail" in capsys.readouterr().err


def test_linkedin_minimal_posting_fallbacks(js, monkeypatch, no_sleep):
    search = {"gas job": {"job_ids": ["9"]}}
    details = {"9": {"sections": {"job_posting": "OnlyCompany"}}}
    li_stub(js, monkeypatch, search, details)
    assert js.linkedin_search([("gas job", "India")], "past_week", 3, verbose=False) == [
        {"src": "linkedin", "title": "gas job", "company": "OnlyCompany", "location": "India", "posted": None,
         "url": "https://www.linkedin.com/jobs/view/9", "text": "OnlyCompany"}]


def test_linkedin_domain_filter_regex(js):
    for good in ("LNG Trader", "Energy Strategy", "Policy Advisor", "Solar EPC", "Consultant", "Origination Head"):
        assert js.LI_DOMAIN.search(good), good
    for bad in ("Barista", "Graphic Designer"):
        assert not js.LI_DOMAIN.search(bad), bad


# ---------------- main ----------------
def row(src, url, title, text, **kw):
    return {"src": src, "title": title, "company": "Co", "location": None, "posted": "2026-01-01", "url": url, "text": text, **kw}


def run_main(js, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["job_search.py", *argv])
    js.main()


@pytest.fixture
def cv(home):
    (home / "Downloads/CV_Umashankar_Master_2026-08-26.md").write_text("lng solar wind python sql hydrogen")


def test_main_filters_sorts_and_writes_per_source(js, cv, monkeypatch, capsys):
    raw = [row("exa", "https://a.com/1?x=1", "Great LNG role", "lng solar wind python"),             # 4/4 -> 100
           row("exa", "https://www.a.com/1/", "Duplicate of the first", "lng solar wind python"),      # same normalised URL
           row("exa", "https://a.com/2", "Intern for LNG", "lng solar wind python"),                   # junk title
           row("exa", "https://a.com/3", "Low coverage", "lng ammonia methanol upstream"),             # 25% < 55
           row("exa", "https://a.com/4", "No keywords", "nothing"),
           row("exa", "", "No url", "lng solar"),
           row("exa", "https://a.com/5", "Second kept", "lng solar"),
           row("adzuna", "https://b.com/1", "Hydrogen analyst", "hydrogen sql", salary_min=1000, salary_max=2000)]
    monkeypatch.setattr(js, "exa_search", lambda q, n: raw[:7])
    monkeypatch.setattr(js, "adzuna_creds", lambda: ("i", "k"))
    monkeypatch.setattr(js, "adzuna_search", lambda cs, days, i, k: raw[7:])
    run_main(js, monkeypatch, "--source", "both")
    out = capsys.readouterr().out
    exa = json.loads((js.OUTDIR / "results_exa.json").read_text())
    adz = json.loads((js.OUTDIR / "results_adzuna.json").read_text())
    assert [r["title"] for r in exa] == ["Great LNG role", "Second kept"]    # 4kw*100 first, then 2kw*100
    assert exa[0]["cov"] == 100 and exa[0]["n"] == 4 and "text" not in exa[0]
    assert [r["title"] for r in adz] == ["Hydrogen analyst"]
    assert "3 new matches >= 55%  {'exa': 2, 'adzuna': 1}" in out
    assert "Hydrogen analyst  [1,000-2,000]" in out
    assert json.loads(js.CACHE.read_text()) == sorted(["a.com/1", "a.com/5", "b.com/1"])


def test_main_seen_cache_and_no_dedupe(js, cv, monkeypatch, capsys):
    js.OUTDIR.mkdir(exist_ok=True)
    js.CACHE.write_text(json.dumps(["a.com/1"]))
    raw = [row("exa", "https://a.com/1", "Seen already", "lng solar"), row("exa", "https://a.com/2", "New one", "lng solar")]
    monkeypatch.setattr(js, "exa_search", lambda q, n: raw)
    run_main(js, monkeypatch, "--source", "exa")
    assert "1 new matches" in capsys.readouterr().out
    assert json.loads(js.CACHE.read_text()) == ["a.com/1", "a.com/2"]
    for r in raw:
        r["text"] = "lng solar"
    run_main(js, monkeypatch, "--source", "exa", "--no-dedupe")
    assert "2 new matches" in capsys.readouterr().out
    assert json.loads(js.CACHE.read_text()) == ["a.com/1", "a.com/2"]       # cache untouched with --no-dedupe


def test_main_min_threshold_boundary_and_per_query(js, cv, monkeypatch, capsys):
    seen_args = []
    raw = [row("exa", "https://a.com/1", "T", "lng solar wind python sql hydrogen"[:0] + "lng solar ammonia")]     # 2/3 = 67
    monkeypatch.setattr(js, "exa_search", lambda q, n: (seen_args.append((q, n)), raw)[1])
    run_main(js, monkeypatch, "--source", "exa", "--min", "67", "--per-query", "3", "--no-dedupe")
    assert "1 new matches >= 67%" in capsys.readouterr().out and seen_args == [(js.EXA_QUERIES, 3)]
    raw[0]["text"] = "lng solar ammonia"
    run_main(js, monkeypatch, "--source", "exa", "--min", "68", "--no-dedupe")
    assert "0 new matches >= 68%" in capsys.readouterr().out


def test_main_adzuna_without_keys_is_skipped_and_keeps_previous_results(js, cv, monkeypatch, capsys):
    js.OUTDIR.mkdir(exist_ok=True)
    (js.OUTDIR / "results_adzuna.json").write_text('["previous"]')
    monkeypatch.setattr(js, "exa_search", lambda q, n: [])
    monkeypatch.setattr(js, "adzuna_creds", lambda: (None, None))
    monkeypatch.setattr(js, "adzuna_search", lambda *a: pytest.fail("must not be called"))
    run_main(js, monkeypatch)
    assert "Adzuna: no keys -- skipping" in capsys.readouterr().err
    assert (js.OUTDIR / "results_adzuna.json").read_text() == '["previous"]'     # no source rows -> file not rewritten


def test_main_country_filter_and_days(js, cv, monkeypatch):
    seen = {}
    monkeypatch.setattr(js, "adzuna_creds", lambda: ("i", "k"))
    monkeypatch.setattr(js, "adzuna_search", lambda cs, days, i, k: (seen.update(cs=cs, days=days), [])[1])
    run_main(js, monkeypatch, "--source", "adzuna", "--countries", "in, de ,xx,", "--days", "9")
    assert seen == {"cs": ["in", "de"], "days": 9}


def test_main_linkedin_only_and_all(js, cv, monkeypatch, capsys):
    li = [row("linkedin", "https://li/1", "Head of LNG", "lng solar wind")]
    got = {}
    monkeypatch.setattr(js, "linkedin_search", lambda q, d, b: (got.update(q=q, d=d, b=b), li)[1])
    monkeypatch.setattr(js, "exa_search", lambda q, n: pytest.fail("exa must not run"))
    run_main(js, monkeypatch, "--source", "linkedin", "--li-budget", "4", "--li-date", "past_24_hours")
    assert got == {"q": js.LI_QUERIES, "d": "past_24_hours", "b": 4}
    assert json.loads((js.OUTDIR / "results_linkedin.json").read_text())[0]["title"] == "Head of LNG"
    assert "LinkedIn (authenticated -- throttled)" in capsys.readouterr().err
    monkeypatch.setattr(js, "exa_search", lambda q, n: [])
    monkeypatch.setattr(js, "adzuna_creds", lambda: (None, None))
    run_main(js, monkeypatch, "--source", "all", "--no-dedupe")
    assert got["b"] == 10


def test_main_without_cv_scores_nothing(js, home, monkeypatch, capsys):
    monkeypatch.setattr(js, "exa_search", lambda q, n: [row("exa", "https://a.com/1", "T", "lng solar")])
    run_main(js, monkeypatch, "--source", "exa", "--min", "0", "--no-dedupe")
    out = capsys.readouterr().out
    assert "1 new matches >= 0%" in out and json.loads((js.OUTDIR / "results_exa.json").read_text())[0]["cov"] == 0


def test_main_output_line_format(js, cv, monkeypatch, capsys):
    raw = [row("exa", "https://a.com/1", "A very good title", "lng solar", location="Pune", company="Acme")]
    monkeypatch.setattr(js, "exa_search", lambda q, n: raw)
    run_main(js, monkeypatch, "--source", "exa", "--no-dedupe")
    lines = capsys.readouterr().out.splitlines()
    assert lines[3] == "100%  2kw [exa   ] 2026-01-01 A very good title"
    assert lines[4] == "            Acme — Pune" and lines[5] == "            https://a.com/1"


@pytest.mark.xfail(strict=True, raises=TypeError,
                   reason="job_search.py main(): an Adzuna result with no title (j.get('title') -> None) passes the filters "
                          "and then crashes the report loop at r['title'][:60]")
def test_main_survives_a_result_without_title(js, cv, monkeypatch, capsys):
    monkeypatch.setattr(js, "adzuna_creds", lambda: ("i", "k"))
    monkeypatch.setattr(js, "adzuna_search", lambda *a: [row("adzuna", "https://b.com/1", None, "lng solar")])
    run_main(js, monkeypatch, "--source", "adzuna", "--no-dedupe")
    assert "1 new matches" in capsys.readouterr().out


def test_linkedin_field_length_caps_and_month_age(js, monkeypatch, no_sleep):
    blob = ("C" * 120) + "\n\n" + ("Gas Lead " + "T" * 200) + "\n\nPune \u00b7 2 months ago \u00b7 x"
    li_stub(js, monkeypatch, {"gas k": {"job_ids": ["1"]}}, {"1": {"sections": {"job_posting": blob}}})
    r = js.linkedin_search([("gas k", "India")], "past_week", 1, verbose=False)[0]
    assert len(r["company"]) == 80 and len(r["title"]) == 150 and r["posted"] == "2 months ago"


def test_main_default_days_and_per_query(js, cv, monkeypatch):
    seen = {}
    monkeypatch.setattr(js, "exa_search", lambda q, n: (seen.update(n=n), [])[1])
    monkeypatch.setattr(js, "adzuna_creds", lambda: ("i", "k"))
    monkeypatch.setattr(js, "adzuna_search", lambda cs, days, i, k: (seen.update(days=days, cs=cs), [])[1])
    run_main(js, monkeypatch)
    assert seen["n"] == 8 and seen["days"] == 3 and seen["cs"] == list(js.COUNTRIES)


def test_main_orders_by_coverage_times_sqrt_keywords(js, home, monkeypatch, capsys):
    (home / "Downloads/CV_Umashankar_Master_2026-08-26.md").write_text("lng solar wind oil gas")
    raw = [row("exa", "https://a.com/1", "Narrow but perfect", "lng"),                                # 100% x sqrt(1) = 100
           row("exa", "https://a.com/2", "Broad and strong", "lng solar wind oil gas ammonia")]       # 83% x sqrt(6) = 204
    monkeypatch.setattr(js, "exa_search", lambda q, n: raw)
    run_main(js, monkeypatch, "--source", "exa", "--no-dedupe")
    res = json.loads((js.OUTDIR / "results_exa.json").read_text())
    assert [r["title"] for r in res] == ["Broad and strong", "Narrow but perfect"]


def test_main_prints_at_most_30_rows(js, cv, monkeypatch, capsys):
    raw = [row("exa", f"https://a.com/{i}", f"Role {i}", "lng solar") for i in range(35)]
    monkeypatch.setattr(js, "exa_search", lambda q, n: raw)
    run_main(js, monkeypatch, "--source", "exa", "--no-dedupe")
    out = capsys.readouterr().out
    assert "35 new matches" in out and out.count("            https://a.com/") == 30


def test_main_salary_shown_only_when_both_bounds_exist(js, cv, monkeypatch, capsys):
    raw = [row("adzuna", "https://b.com/1", "Only min", "lng solar", salary_min=5000, salary_max=None),
           row("adzuna", "https://b.com/2", "Both", "lng solar", salary_min=5000, salary_max=9000)]
    monkeypatch.setattr(js, "adzuna_creds", lambda: ("i", "k"))
    monkeypatch.setattr(js, "adzuna_search", lambda *a: raw)
    run_main(js, monkeypatch, "--source", "adzuna", "--no-dedupe")
    out = capsys.readouterr().out
    assert "Both  [5,000-9,000]" in out and "Only min\n" in out + "\n" and "Only min  [" not in out

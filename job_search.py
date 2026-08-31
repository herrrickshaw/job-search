#!/usr/bin/env python3
"""Unified job search: Exa (semantic web) + Adzuna (job-board API), scored against your CV.

Exa works with no credentials (via mcporter). Adzuna needs free keys from
developer.adzuna.com in ADZUNA_APP_ID / ADZUNA_APP_KEY -- env, or
~/.config/market-secrets/credentials.env. Missing Adzuna keys => Exa-only run.

  python3 job_search.py                        # both sources, last 3 days
  python3 job_search.py --source exa --min 50
  python3 job_search.py --source adzuna --countries in,de --days 7
"""
import os, sys, json, re, time, subprocess, argparse, pathlib
import urllib.parse, urllib.request

HOME = pathlib.Path.home()
STORE = HOME / ".config/market-secrets/credentials.env"
CV = HOME / "Downloads/CV_Umashankar_Master_2026-08-26.md"
OUTDIR = HOME / "job-search"
CACHE = OUTDIR / "seen.json"

# ---------------------------------------------------------------- scoring
VOCAB = ["lng","natural gas","midstream","downstream","upstream","refinery","petrochemical",
"oil and gas","hydrogen","ammonia","methanol","biogas","ethanol","biofuel","sustainable aviation fuel",
"carbon capture","ccus","decarbonisation","decarbonization","net zero","emissions","energy transition",
"renewable","solar","wind","esg","sustainability","climate","foreign direct investment",
"investment promotion","investor","fdi","origination","transaction advisory","due diligence",
"financial modelling","financial modeling","valuation","npv","irr","feasibility","project finance",
"mergers acquisitions","public private partnership","ppp","joint venture","infrastructure","policy",
"regulatory","government","advocacy","stakeholder","benchmarking","market entry","market research",
"process engineering","aspen","hysys","cost engineering","capex","opex","procurement","tender",
"total cost of ownership","python","power bi","sql","excel","analytics","data analysis","strategy",
"management consulting","business development","commercial","corporate development","go-to-market",
"business transformation","negotiation","energy efficiency","electrification","project management"]

JUNK = re.compile(r"(intern(ship)?\b|fresher|trainee|apprentice|werkstudent|praktikum|"
                  r"software engineer|developer|devops|full.?stack|qa engineer|nurse|teacher)", re.I)

def load_cv():
    if not CV.exists():
        print(f"! CV not found at {CV} -- scoring disabled", file=sys.stderr); return ""
    return re.sub(r"\s+", " ", CV.read_text()).lower()

def score(text, cv):
    d = re.sub(r"[^a-z0-9&+\- ]", " ", (text or "").lower())
    pres = [v for v in VOCAB if v in d]
    if not pres: return None
    hit = [v for v in pres if v in cv]
    return {"cov": round(100*len(hit)/len(pres)), "n": len(pres),
            "miss": [v for v in pres if v not in cv][:10]}

def norm_url(u):
    u = (u or "").split("?")[0].rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", u).lower()

# ---------------------------------------------------------------- Exa
EXA_QUERIES = [
 "LNG or natural gas business development manager job opening in India",
 "energy transition or decarbonization manager job opening India Chennai Bengaluru Hyderabad",
 "investment promotion, FDI or infrastructure advisory job opening India",
 "energy strategy consulting manager vacancy India",
 "green hydrogen, biogas or sustainable aviation fuel job opening India",
 "LNG or gas commercial and origination job opening Singapore or UAE",
 "energy or infrastructure investment manager vacancy Germany Austria English speaking",
 "public affairs or government relations manager job opening energy sector",
]

def exa_search(queries, per_query, verbose=True):
    rows = []
    for q in queries:
        try:
            p = subprocess.run(["mcporter","call","exa.web_search_exa",
                                f"query={q}", f"numResults={per_query}", "--output","json"],
                               capture_output=True, text=True, timeout=180)
            if p.returncode != 0:
                print(f"  ! exa: {p.stderr.strip()[:120]}", file=sys.stderr); continue
            blocks = json.loads(p.stdout).get("content", [])
            text = "\n".join(b.get("text","") for b in blocks if b.get("type")=="text")
        except Exception as e:
            print(f"  ! exa '{q[:30]}': {e}", file=sys.stderr); continue
        for chunk in re.split(r"\n-{3,}\n", text):
            m_t = re.search(r"^Title:\s*(.+)$", chunk, re.M)
            m_u = re.search(r"^URL:\s*(\S+)$", chunk, re.M)
            if not (m_t and m_u): continue
            m_p = re.search(r"^Published:\s*(\S+)$", chunk, re.M)
            m_a = re.search(r"^Author:\s*(.+)$", chunk, re.M)
            rows.append({"src":"exa","title":m_t.group(1).strip()[:150],
                "company":(m_a.group(1).strip() if m_a and m_a.group(1).strip()!="N/A" else None),
                "location":None,
                "posted":(m_p.group(1)[:10] if m_p and m_p.group(1)!="N/A" else None),
                "url":m_u.group(1).strip(), "text":chunk})
        if verbose: print(f"  exa: {q[:52]:<52} -> {len(rows)} cumulative", file=sys.stderr)
        time.sleep(0.5)
    return rows

# ---------------------------------------------------------------- Adzuna
COUNTRIES = {"in":"India","de":"Germany","at":"Austria","ch":"Switzerland",
             "sg":"Singapore","gb":"United Kingdom","nl":"Netherlands","us":"United States"}
AD_QUERIES = ["LNG natural gas business development","energy transition decarbonisation manager",
              "investment manager energy infrastructure","energy strategy consultant",
              "green hydrogen","public affairs policy energy","gas market analyst"]

def adzuna_creds():
    i,k = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (i and k) and STORE.exists():
        env = dict(re.findall(r"^([A-Za-z0-9_]+)=(.*)$", STORE.read_text(), re.M))
        i,k = env.get("ADZUNA_APP_ID"), env.get("ADZUNA_APP_KEY")
    return (i.strip().strip('"'), k.strip().strip('"')) if (i and k) else (None,None)

def adzuna_search(countries, days, app_id, app_key, verbose=True):
    rows=[]
    for cid in countries:
        for q in AD_QUERIES:
            p = urllib.parse.urlencode({"app_id":app_id,"app_key":app_key,"results_per_page":50,
                                        "what":q,"max_days_old":days,"sort_by":"date"})
            try:
                with urllib.request.urlopen(
                        f"https://api.adzuna.com/v1/api/jobs/{cid}/search/1?{p}", timeout=30) as r:
                    res = json.loads(r.read()).get("results",[])
            except Exception as e:
                print(f"  ! adzuna {cid}: {e}", file=sys.stderr); continue
            for j in res:
                rows.append({"src":"adzuna","title":j.get("title"),
                    "company":(j.get("company") or {}).get("display_name"),
                    "location":(j.get("location") or {}).get("display_name"),
                    "posted":(j.get("created") or "")[:10], "url":j.get("redirect_url"),
                    "salary_min":j.get("salary_min"),"salary_max":j.get("salary_max"),
                    "text":j.get("description"),"country":COUNTRIES.get(cid,cid)})
            time.sleep(0.4)
        if verbose: print(f"  adzuna {cid}: {len(rows)} cumulative", file=sys.stderr)
    return rows


# ---------------------------------------------------------------- LinkedIn (authenticated)
# Opt-in only: every call scrapes under the user's own session. Kept rate-limited
# and call-budgeted so bulk sweeps stay on Exa/Adzuna, which carry no account risk.
LI_QUERIES = [
 ("LNG natural gas business development","India"),
 ("energy transition decarbonization manager","India"),
 ("investment promotion FDI advisory","India"),
 ("energy strategy consulting manager","India"),
 ("green hydrogen compressed biogas","India"),
 ("LNG gas commercial origination","Singapore"),
 ("energy infrastructure investment manager","Germany"),
 ("government relations public affairs energy","India"),
]
LI_DOMAIN = re.compile(r"(lng|gas|oil|energ|hydrogen|biogas|renewab|solar|decarbon|sustainab|climate|"
                       r"power|infrastruc|invest|fdi|advisor|consult|strateg|transaction|policy|"
                       r"government|public affair|commercial|business development|origination)", re.I)

def _mcp(tool, **kw):
    args = [f"{k}={v}" for k,v in kw.items() if v is not None]
    p = subprocess.run(["mcporter","call",f"linkedin.{tool}",*args,"--output","json"],
                       capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:160])
    return json.loads(p.stdout)

def linkedin_search(queries, days_filter, detail_budget, verbose=True):
    """Search, title-filter, then fetch full JD only for the most plausible hits."""
    seen_ids, cands = set(), []
    for kw, loc in queries:
        try:
            r = _mcp("search_jobs", keywords=kw, location=loc, date_posted=days_filter,
                     experience_level="mid_senior,director", max_pages=1, sort_by="date")
        except Exception as e:
            print(f"  ! linkedin '{kw[:28]}': {e}", file=sys.stderr); continue
        txt = (r.get("sections") or {}).get("search_results","")
        ids = r.get("job_ids") or []
        # titles appear as a line immediately followed by "<same> with verification",
        # or as the line preceding a company/location pair; take unique candidate lines.
        titles = [l.strip() for l in txt.split("\n")
                  if l.strip() and not l.startswith(("Jump to","Jobs you may"))]
        for jid in ids:
            if jid in seen_ids: continue
            seen_ids.add(jid)
            cands.append({"id": jid, "hint": kw, "loc": loc,
                          "title_pool": titles[:4]})
        if verbose: print(f"  linkedin: {kw[:44]:<44} -> {len(seen_ids)} ids", file=sys.stderr)
        time.sleep(2.5)                      # deliberate throttle
    rows, spent = [], 0
    for c in cands:
        if spent >= detail_budget: break
        try:
            d = _mcp("get_job_details", job_id=c["id"])
        except Exception as e:
            print(f"  ! detail {c['id']}: {e}", file=sys.stderr); continue
        spent += 1
        sects = d.get("sections") or {}
        blob = sects.get("job_posting") or " ".join(v for v in sects.values() if isinstance(v,str))
        # sections.job_posting starts: "<Company>\n\n<Title>\n\n<Location> \u00b7 <age> ..."
        parts = [b.strip() for b in (blob or "").split("\n\n") if b.strip()]
        company = parts[0][:80] if parts else None
        title   = parts[1][:150] if len(parts) > 1 else c["hint"]
        meta    = parts[2] if len(parts) > 2 else ""
        location = meta.split("\u00b7")[0].strip()[:80] or c["loc"]
        m_age = re.search(r"(\d+)\s+(hour|day|week|month)s?\s+ago", meta)
        posted = f"{m_age.group(1)} {m_age.group(2)}s ago" if m_age else None
        if not LI_DOMAIN.search(title):
            time.sleep(2.0); continue
        rows.append({"src":"linkedin","title":title,"company":company,
            "location":location,"posted":posted,
            "url": d.get("url") or f"https://www.linkedin.com/jobs/view/{c['id']}",
            "text": blob})
        time.sleep(2.0)                      # deliberate throttle
    if verbose: print(f"  linkedin: {spent} detail calls spent", file=sys.stderr)
    return rows

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["both","exa","adzuna","linkedin","all"], default="both")
    ap.add_argument("--li-budget", type=int, default=10, help="max LinkedIn detail calls")
    ap.add_argument("--li-date", default="past_week",
                    choices=["past_hour","past_24_hours","past_week","past_month"])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--min", type=int, default=55)
    ap.add_argument("--per-query", type=int, default=8)
    ap.add_argument("--countries", default=",".join(COUNTRIES))
    ap.add_argument("--no-dedupe", action="store_true", help="ignore the seen-cache")
    a = ap.parse_args()
    OUTDIR.mkdir(exist_ok=True)
    cv = load_cv()
    seen = set() if a.no_dedupe else set(json.loads(CACHE.read_text()) if CACHE.exists() else [])

    raw=[]
    if a.source in ("both","exa","all"):
        print("Exa (semantic web)...", file=sys.stderr)
        raw += exa_search(EXA_QUERIES, a.per_query)
    if a.source in ("both","adzuna","all"):
        aid, akey = adzuna_creds()
        if not aid:
            print("Adzuna: no keys -- skipping. Get free keys at https://developer.adzuna.com/ "
                  f"and add ADZUNA_APP_ID/ADZUNA_APP_KEY to {STORE}", file=sys.stderr)
        else:
            print("Adzuna (job boards)...", file=sys.stderr)
            cs=[c.strip() for c in a.countries.split(",") if c.strip() in COUNTRIES]
            raw += adzuna_search(cs, a.days, aid, akey)

    if a.source in ("linkedin","all"):
        print("LinkedIn (authenticated -- throttled)...", file=sys.stderr)
        raw += linkedin_search(LI_QUERIES, a.li_date, a.li_budget)

    rows, byurl = [], {}
    for r in raw:
        k = norm_url(r["url"])
        if not k or k in seen or k in byurl: continue
        if JUNK.search(r.get("title") or ""): continue
        s = score(r.get("text"), cv)
        if not s or s["cov"] < a.min: continue
        r.update(s); r.pop("text",None); byurl[k]=r; rows.append(r)
    rows.sort(key=lambda r: -(r["cov"] * (r["n"]**0.5)))

    (OUTDIR/"results.json").write_text(json.dumps(rows, indent=1))
    if not a.no_dedupe:
        CACHE.write_text(json.dumps(sorted(seen | set(byurl))))
    src = {}
    for r in rows: src[r["src"]] = src.get(r["src"],0)+1
    print(f"\n{len(rows)} new matches >= {a.min}%  {src}  -> {OUTDIR/'results.json'}\n")
    for r in rows[:30]:
        sal = ""
        if r.get("salary_min") and r.get("salary_max"):
            sal = f'  [{int(r["salary_min"]):,}-{int(r["salary_max"]):,}]'
        loc = f' — {r["location"]}' if r.get("location") else ""
        print(f'{r["cov"]:>3}% {r["n"]:>2}kw [{r["src"]:<6}] {r.get("posted") or "n/a":<10} {r["title"][:60]}{sal}')
        print(f'            {r.get("company") or "?"}{loc}')
        print(f'            {r["url"][:112]}')

if __name__ == "__main__":
    main()

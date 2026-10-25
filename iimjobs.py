#!/usr/bin/env python3
"""Scrape iimjobs category pages (JS SPA) via Jina Reader, parse listings, score vs CV."""
import re, sys, json, time, urllib.request, pathlib

CV = pathlib.Path.home()/"Downloads/CV_Umashankar_Master_2026-08-26.md"
SLUGS = ["consulting-energy-sector","consulting-oil-and-gas","consulting-infra",
         "investment-promotion","public-policy","sustainability","strategy-consulting",
         "consulting-egov-psu","consulting-mfg"]
VOCAB = ["lng","natural gas","oil","gas","refinery","petrochemical","hydrogen","biogas","ethanol",
"biofuel","carbon","decarbonisation","decarbonization","energy transition","renewable","solar","wind",
"esg","sustainability","climate","investment promotion","investment","fdi","origination","transaction",
"due diligence","financial modelling","financial modeling","valuation","feasibility","project finance",
"ppp","infrastructure","policy","regulatory","government","public sector","advocacy","stakeholder",
"market entry","strategy","consulting","business development","commercial","project management",
"energy","power","chemical","manufacturing","process"]

def jina(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request("https://r.jina.ai/"+url,
                                         headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read().decode("utf-8","ignore")
        except Exception as e:
            if i == tries-1: print(f"  ! {url}: {e}", file=sys.stderr)
            time.sleep(3)
    return ""

# label = "Company - Title N - M yrs<junk>Location Posted X ago Tag Tag ..."
JOBURL = re.compile(r"\((https://www\.iimjobs\.com/j/[a-z0-9-]+)[^)]*\)")
def parse(md):
    """Each listing occupies one line; labels contain nested ] from images,
    so split on the URL rather than trying to bracket-match."""
    out = []
    for line in md.split("\n"):
        if "iimjobs.com/j/" not in line: continue
        m = JOBURL.search(line)
        if not m: continue
        label = line[:m.start()]
        t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", label)      # strip images
        t = re.sub(r"[\[\]]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        if len(t) < 15: continue
        m_y = re.search(r"(\d+)\s*-\s*(\d+)\s*yrs", t)
        m_p = re.search(r"Posted\s+(\d+\s+\w+\s+ago|today|yesterday)", t, re.I)
        title = t.split(m_y.group(0))[0].strip() if m_y else t[:120]
        loc = ""
        if m_y:
            tail = t[t.index(m_y.group(0))+len(m_y.group(0)):]
            loc = re.split(r"Posted", tail)[0].strip(" .\u00b7")[:60]
        out.append({"title": title[:150], "url": m.group(1),
                    "yrs": (int(m_y.group(1)), int(m_y.group(2))) if m_y else None,
                    "posted": (m_p.group(1) if m_p else None),
                    "location": loc, "tags": t})
    return out

def main():
    cv = re.sub(r"\s+"," ",CV.read_text()).lower() if CV.exists() else ""
    seen, rows = set(), []
    for s in SLUGS:
        md = jina(f"https://www.iimjobs.com/k/{s}-jobs")
        got = parse(md)
        print(f"  {s:<26} {len(got):>3} listings", file=sys.stderr)
        for r in got:
            if r["url"] in seen: continue
            seen.add(r["url"]); r["cat"]=s; rows.append(r)
        time.sleep(1.5)
    for r in rows:
        d = re.sub(r"[^a-z0-9&+\- ]"," ",r["tags"].lower())
        pres=[v for v in VOCAB if v in d]
        hit=[v for v in pres if v in cv]
        r["cov"]=round(100*len(hit)/len(pres)) if pres else 0
        r["n"]=len(pres)
        r["fit"]= bool(r["yrs"]) and r["yrs"][0] <= 12 and r["yrs"][1] >= 8   # ~10 yrs band
    rows.sort(key=lambda r:(-(r["fit"]), -(r["cov"]*(r["n"]**0.5))))
    pathlib.Path("iimjobs_results.json").write_text(json.dumps(rows,indent=1))
    fit=[r for r in rows if r["fit"]]
    print(f"\n{len(rows)} unique listings | {len(fit)} in your ~10-yr band\n")
    for r in fit[:28]:
        y=f'{r["yrs"][0]}-{r["yrs"][1]}y'
        print(f'{r["cov"]:>3}% {r["n"]:>2}kw {y:<7} {r["posted"] or "?":<14} {r["title"][:64]}')
        print(f'            {r["location"]}  ·  {r["url"]}')
if __name__ == "__main__":
    main()

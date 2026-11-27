"""Discover which companies have public Greenhouse / Lever / Ashby / Workday boards.

Usage:  .venv\\Scripts\\python scripts\\find_boards.py
Writes boards.yaml. Add more candidates below and rerun any time.
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
HEADERS = {"User-Agent": "JobApp/0.1 (personal job search tool)"}

# (Display name, slug guesses). Big / well-known employers with Canadian or remote roles.
CANDIDATES = {
    "Stripe": ["stripe"], "Databricks": ["databricks"], "Coinbase": ["coinbase"],
    "Instacart": ["instacart"], "Faire": ["faire"], "Pinterest": ["pinterest"], "Reddit": ["reddit"],
    "Figma": ["figma"], "Datadog": ["datadog"], "Cloudflare": ["cloudflare"], "Duolingo": ["duolingo"],
    "Lyft": ["lyft"], "Dropbox": ["dropbox"], "Airbnb": ["airbnb"], "Robinhood": ["robinhood"],
    "Twitch": ["twitch"], "Roblox": ["roblox"], "Discord": ["discord"], "GitLab": ["gitlab"],
    "Anthropic": ["anthropic"], "OpenAI": ["openai"], "Cohere": ["cohere"], "Wealthsimple": ["wealthsimple"],
    "1Password": ["1password"], "Geotab": ["geotab"], "Top Hat": ["tophat"], "Wattpad": ["wattpad"],
    "Ada": ["ada", "ada18"], "Clio": ["clio"], "KOHO": ["koho"], "Ritual": ["ritual"], "League": ["league"],
    "Questrade": ["questrade"], "Snowflake": ["snowflake", "snowflakecomputing"], "Scale AI": ["scaleai"],
    "Notion": ["notion"], "Ramp": ["ramp"], "Plaid": ["plaid"], "Brex": ["brex"], "Asana": ["asana"],
    "Okta": ["okta"], "Twilio": ["twilio"], "HubSpot": ["hubspot"], "Unity": ["unity3d", "unity"],
    "Samsara": ["samsara"], "Affirm": ["affirm"], "Square / Block": ["block", "square"],
    "Spotify": ["spotify"], "Palantir": ["palantir"], "Nuro": ["nuro"], "Waabi": ["waabi"],
    "Tenstorrent": ["tenstorrent"], "Untether AI": ["untetherai"], "Vector Institute": ["vectorinstitute"],
    "Ecobee": ["ecobee"], "Thinkific": ["thinkific"], "Hootsuite": ["hootsuite"], "Lightspeed": ["lightspeedhq", "lightspeed"],
    "Benevity": ["benevity"], "Dialpad": ["dialpad"], "Hopper": ["hopper"], "Nuvei": ["nuvei"],
    "PointClickCare": ["pointclickcare"], "Arctic Wolf": ["arcticwolf"], "Kinaxis": ["kinaxis"],
    "D2L": ["d2l"], "Shopify": ["shopify"], "Super.com": ["super"], "Float": ["float"],
    "Faire Canada": [], "Mozilla": ["mozilla"], "Elastic": ["elastic"], "MongoDB": ["mongodb"],
    "Confluent": ["confluent"], "Grammarly": ["grammarly"], "Zapier": ["zapier"], "Canva": ["canva"],
}

# Workday: (company, host, tenant, site) guesses. Wrong guesses are simply dropped.
WORKDAY = [
    ("RBC", "rbc.wd3.myworkdayjobs.com", "rbc", "RBCGLOBAL1"),
    ("TD Bank", "td.wd3.myworkdayjobs.com", "td", "TD_Bank_Careers"),
    ("BMO", "bmo.wd3.myworkdayjobs.com", "bmo", "External"),
    ("CIBC", "cibc.wd3.myworkdayjobs.com", "cibc", "search"),
    ("Manulife", "manulife.wd3.myworkdayjobs.com", "manulife", "MFCJH_Jobs"),
    ("Sun Life", "sunlife.wd3.myworkdayjobs.com", "sunlife", "Experienced-Jobs"),
    ("Sun Life", "sunlife.wd3.myworkdayjobs.com", "sunlife", "Campus"),
    ("NVIDIA", "nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite"),
    ("Intel", "intel.wd1.myworkdayjobs.com", "intel", "External"),
    ("AMD", "amd.wd1.myworkdayjobs.com", "amd", "External"),
    ("Salesforce", "salesforce.wd12.myworkdayjobs.com", "salesforce", "External_Career_Site"),
    ("Adobe", "adobe.wd5.myworkdayjobs.com", "adobe", "external_experienced"),
    ("Adobe", "adobe.wd5.myworkdayjobs.com", "adobe", "external_university"),
    ("Thomson Reuters", "thomsonreuters.wd5.myworkdayjobs.com", "thomsonreuters", "External_Career_Site"),
    ("Mastercard", "mastercard.wd1.myworkdayjobs.com", "mastercard", "CorporateCareers"),
    ("Autodesk", "autodesk.wd1.myworkdayjobs.com", "autodesk", "Ext"),
    ("Workday", "workday.wd5.myworkdayjobs.com", "workday", "Workday"),
    ("Capital One", "capitalone.wd12.myworkdayjobs.com", "capitalone", "Capital_One"),
    ("Rogers", "rogers.wd3.myworkdayjobs.com", "rogers", "RCI"),
    ("Telus", "telus.wd3.myworkdayjobs.com", "telus", "TELUS"),
    ("Intact", "intactfc.wd3.myworkdayjobs.com", "intactfc", "IFC"),
    ("Scotiabank", "scotiabank.wd3.myworkdayjobs.com", "scotiabank", "Scotiabank"),
    ("Canadian Tire", "cantire.wd3.myworkdayjobs.com", "cantire", "Canadian_Tire_Corporation"),
    ("OMERS", "omers.wd3.myworkdayjobs.com", "omers", "OMERS_External"),
    ("CPP Investments", "cppib.wd10.myworkdayjobs.com", "cppib", "CPPIB"),
    ("Ontario Teachers'", "otppb.wd3.myworkdayjobs.com", "otppb", "OTPP"),
    ("Celestica", "celestica.wd3.myworkdayjobs.com", "celestica", "Celestica_Careers"),
    ("Ciena", "ciena.wd5.myworkdayjobs.com", "ciena", "Careers"),
    ("Visa", "visa.wd5.myworkdayjobs.com", "visa", "Visa"),
    ("Accenture", "accenture.wd103.myworkdayjobs.com", "accenture", "AccentureCareers"),
    ("Cisco", "cisco.wd5.myworkdayjobs.com", "cisco", "Cisco_Careers"),
    ("Dayforce", "dayforce.wd5.myworkdayjobs.com", "dayforce", "Dayforce"),
    ("Hydro One", "hydroone.wd3.myworkdayjobs.com", "hydroone", "HydroOne"),
    ("Loblaw", "loblaw.wd3.myworkdayjobs.com", "loblaw", "Loblaw_Careers"),
]


def try_board(company, slug, c):
    tests = [
        ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
         lambda r: len(r.json().get("jobs", []))),
        ("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=500",
         lambda r: len(r.json()) if isinstance(r.json(), list) else 0),
        ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
         lambda r: len(r.json().get("jobs", []))),
    ]
    hits = []
    for kind, url, count in tests:
        try:
            r = c.get(url)
            if r.status_code == 200:
                n = count(r)
                if n > 0:
                    hits.append((kind, {"company": company, "slug": slug}, n))
        except Exception:
            pass
    return hits


def try_workday(entry, c):
    company, host, tenant, site = entry
    try:
        r = c.post(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                   json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
        if r.status_code == 200 and r.json().get("total", 0) > 0:
            return ("workday", {"company": company, "host": host, "tenant": tenant, "site": site},
                    r.json()["total"])
    except Exception:
        pass
    return None


def main():
    found = {"greenhouse": [], "lever": [], "ashby": [], "workday": []}
    with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as c, ThreadPoolExecutor(8) as ex:
        board_jobs = [ex.submit(try_board, co, s, c) for co, slugs in CANDIDATES.items() for s in slugs]
        wd_jobs = [ex.submit(try_workday, e, c) for e in WORKDAY]
        seen_companies = set()
        for f in board_jobs:
            for kind, entry, n in f.result():
                key = (kind, entry["company"])
                if key in seen_companies:
                    continue
                seen_companies.add(key)
                found[kind].append(entry)
                print(f"  OK  {kind:10} {entry['company']:22} {n} open jobs")
        for f in wd_jobs:
            res = f.result()
            if res:
                kind, entry, n = res
                found[kind].append(entry)
                print(f"  OK  {kind:10} {entry['company']:22} {n} open jobs")
    total = sum(len(v) for v in found.values())
    with open(ROOT / "boards.yaml", "w", encoding="utf-8") as f:
        f.write("# Generated by scripts/find_boards.py. Safe to edit by hand.\n")
        yaml.safe_dump(found, f, sort_keys=False, allow_unicode=True)
    print(f"\nWrote boards.yaml with {total} company boards.")
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())

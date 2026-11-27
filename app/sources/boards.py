"""Official public job-board APIs: Greenhouse, Lever, Ashby, Workday.

These are the same JSON feeds the companies' own career pages use, so jobs show up here
as soon as they're published, often before LinkedIn picks them up.

Each fetcher takes:
  want(title, location) -> bool   cheap pre-filter, applied before any per-job detail request
  seen(source, external_id) -> bool   already in the database (no detail fetch needed)
"""
from datetime import datetime, timezone

import httpx

from ..text import strip_html
from . import HEADERS, RawJob

TIMEOUT = httpx.Timeout(20.0)


def _stub(source, ext_id, title, company, url):
    return RawJob(source=source, external_id=ext_id, title=title, company=company, url=url,
                  extra={"seen_only": True})


def greenhouse(c: httpx.Client, slug: str, company: str, want, seen):
    r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        if not want(j["title"], loc):
            continue
        ext_id = f"{slug}:{j['id']}"
        if seen("greenhouse", ext_id):
            yield _stub("greenhouse", ext_id, j["title"], company, j["absolute_url"]); continue
        desc, posted = "", j.get("updated_at")
        try:
            d = c.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{j['id']}").json()
            desc, posted = strip_html(d.get("content")), d.get("first_published") or posted
        except Exception:
            pass
        yield RawJob(source="greenhouse", external_id=ext_id, title=j["title"], company=company,
                     url=j["absolute_url"], location=loc, description=desc, posted_at=posted)


def lever(c: httpx.Client, slug: str, company: str, want, seen):
    r = c.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
    r.raise_for_status()
    for j in r.json():
        cats = j.get("categories") or {}
        loc = "; ".join(l for l in (cats.get("allLocations") or [cats.get("location") or ""]) if l)
        if not want(j["text"], loc):
            continue
        created = j.get("createdAt")
        yield RawJob(
            source="lever", external_id=f"{slug}:{j['id']}", title=j["text"], company=company,
            url=j["hostedUrl"], location=loc, is_remote=(j.get("workplaceType") == "remote"),
            description=j.get("descriptionPlain") or strip_html(j.get("description")),
            posted_at=datetime.fromtimestamp(created / 1000, timezone.utc).isoformat() if created else None)


def ashby(c: httpx.Client, slug: str, company: str, want, seen):
    r = c.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [a.get("location", "") for a in j.get("secondaryLocations") or []]
        loc = "; ".join(l for l in locs if l)
        if not want(j["title"], loc):
            continue
        yield RawJob(
            source="ashby", external_id=f"{slug}:{j['id']}", title=j["title"], company=company,
            url=j.get("jobUrl") or j.get("applyUrl", ""), location=loc, is_remote=bool(j.get("isRemote")),
            description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
            posted_at=j.get("publishedAt"))


def workday(c: httpx.Client, host: str, tenant: str, site: str, company: str, want, seen,
            search_terms=("intern", "co-op", "student"), pages=3):
    """Workday's search is relevance-ranked, so the first few pages per term cover student roles."""
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    found = {}
    for term in search_terms:
        for offset in range(0, pages * 20, 20):
            r = c.post(f"{base}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": offset,
                                             "searchText": term})
            r.raise_for_status()
            posts = r.json().get("jobPostings", [])
            for p in posts:
                if "externalPath" in p:
                    found.setdefault(p["externalPath"], p)
            if len(posts) < 20:
                break
    for path, p in found.items():
        title, loc_text = p.get("title", ""), p.get("locationsText", "")
        multi = "location" in loc_text.lower()  # "3 Locations": unknown until details
        if not want(title, "" if multi else loc_text):
            continue
        ext_id = f"{tenant}:{path.rsplit('_', 1)[-1]}"
        url = f"https://{host}/{site}{path}"
        if seen("workday", ext_id):
            yield _stub("workday", ext_id, title, company, url); continue
        location, desc, posted, remote = loc_text, "", None, False
        try:
            d = c.get(f"{base}{path}").json().get("jobPostingInfo", {})
            location = "; ".join(l for l in [d.get("location", "")] + (d.get("additionalLocations") or []) if l) or loc_text
            desc = strip_html(d.get("jobDescription"))
            posted = d.get("startDate")
            remote = (d.get("remoteType") or "").lower().startswith("remote")
        except Exception:
            pass
        yield RawJob(source="workday", external_id=ext_id, title=title, company=company, url=url,
                     location=location, is_remote=remote, description=desc, posted_at=posted)


def client() -> httpx.Client:
    return httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True,
                        limits=httpx.Limits(max_connections=20))

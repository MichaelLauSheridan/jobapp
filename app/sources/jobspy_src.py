"""LinkedIn / Indeed / Glassdoor search via the open-source JobSpy library.

Note: scraping LinkedIn is against its Terms of Service. Searches are kept small and infrequent
(see config.yaml -> poll_minutes.jobspy). Prefer LinkedIn alert emails as the main LinkedIn source.
"""
import logging
import math
import time

from . import RawJob

log = logging.getLogger(__name__)


def _s(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v)


def search(cfg: dict):
    from jobspy import scrape_jobs  # heavy import; load lazily

    js = cfg["jobspy"]
    errors = []
    for loc in js["locations"]:
        for term in js["search_terms"]:
            for site in js["sites"]:
                try:
                    df = scrape_jobs(
                        site_name=[site], search_term=term, location=loc["location"],
                        distance=loc.get("distance") or None, is_remote=loc.get("is_remote", False),
                        results_wanted=js["results_wanted"], hours_old=js["hours_old"],
                        country_indeed="Canada", linkedin_fetch_description=False, verbose=0,
                    )
                except Exception as e:  # one site failing must not stop the rest
                    errors.append(f"{site}/{term}: {e}"[:200])
                    continue
                for row in df.to_dict("records"):
                    url = _s(row.get("job_url"))
                    if not url:
                        continue
                    loc_txt = _s(row.get("location"))
                    yield RawJob(
                        source=_s(row.get("site")) or site,
                        external_id=_s(row.get("id")) or url,
                        title=_s(row.get("title")), company=_s(row.get("company")) or "Unknown",
                        url=_s(row.get("job_url_direct")) or url, location=loc_txt,
                        is_remote=bool(row.get("is_remote")) if _s(row.get("is_remote")) else False,
                        description=_s(row.get("description")),
                        posted_at=_s(row.get("date_posted")) or None,
                        extra={"listing_url": url},
                    )
                time.sleep(3)  # be polite between requests
    if errors:
        log.warning("JobSpy errors: %s", errors)
    search.last_errors = errors


search.last_errors = []

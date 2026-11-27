"""Windows desktop notifications."""
import logging

log = logging.getLogger(__name__)
DASHBOARD = "http://localhost:8000"


def toast(title: str, msg: str, url: str = DASHBOARD):
    try:
        from winotify import Notification, audio

        n = Notification(app_id="JobApp", title=title[:60], msg=msg[:200], launch=url, duration="long")
        n.set_audio(audio.Default, loop=False)
        n.add_actions(label="Open", launch=url)
        n.show()
    except Exception as e:  # never let a notification failure break polling
        log.warning("Toast failed: %s", e)


def notify_jobs(jobs: list[dict], max_per_run: int):
    if not jobs:
        return
    if len(jobs) > max_per_run:
        top = sorted(jobs, key=lambda j: -j["score"])[:3]
        toast(f"{len(jobs)} new internships found",
              "Top: " + "; ".join(f"{j['company']} – {j['title']}" for j in top))
        return
    for j in jobs:
        toast(f"New: {j['company']} ({j['score']})", f"{j['title']}\n{j['location'] or ''}",
              f"{DASHBOARD}/job/{j['id']}")

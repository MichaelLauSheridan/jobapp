from dataclasses import dataclass, field


@dataclass
class RawJob:
    source: str           # e.g. "greenhouse", "linkedin"
    external_id: str      # unique within the source
    title: str
    company: str
    url: str
    location: str = ""
    is_remote: bool = False
    description: str = ""
    posted_at: str | None = None   # ISO date/time if known
    extra: dict = field(default_factory=dict)


HEADERS = {"User-Agent": "JobApp/0.1 (personal job search tool)"}

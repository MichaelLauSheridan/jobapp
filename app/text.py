"""Small text helpers shared by filtering, scoring and dedupe."""
import html
import re

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(html.unescape(s))  # Greenhouse double-escapes
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", s)
    s = _TAG.sub(" ", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def has_term(text: str, term: str) -> bool:
    """Whole-word, case-insensitive match (so 'ai' doesn't match 'maintain'). Allows a plural 's'
    and treats '-', en dash and space as the same ('4-month' == '4 months')."""
    t = re.sub(r"[‐-―-]", " ", (text or "").lower())
    pat = re.escape(re.sub(r"[‐-―-]", " ", term.lower())).replace(r"\ ", r"\s+")
    return re.search(r"(?<![a-z0-9])" + pat + r"s?(?![a-z0-9])", t) is not None


def any_term(text: str, terms) -> list[str]:
    return [t for t in terms if has_term(text, t)]


# ---- location ----
CANADA_HINTS = ["canada", "ontario", "toronto", "mississauga", "oakville", "brampton", "markham",
                "vaughan", "richmond hill", "milton", "burlington", "north york", "etobicoke",
                "scarborough", "waterloo", "kitchener", "ottawa", "montreal", "montréal", "vancouver",
                "calgary", "edmonton", "hamilton", "guelph", "london, on", "quebec", "british columbia",
                "alberta", "halifax", "winnipeg"]
CANADA_PROVINCE = re.compile(r",\s*(ON|BC|QC|AB|MB|NS|NB|SK|NL|PE)\b")
GTA = ["toronto", "mississauga", "oakville", "brampton", "markham", "vaughan", "richmond hill",
       "milton", "burlington", "north york", "etobicoke", "scarborough", "pickering", "ajax",
       "whitby", "oshawa", "newmarket", "aurora", "gta", "greater toronto"]
NON_CANADA = ["united states", "usa", "u.s.", "us-", "us remote", "remote - us", "remote, us", "(us)",
              "united kingdom", "uk", "london, england", "ireland", "india", "germany", "france",
              "singapore", "australia", "japan", "brazil", "mexico", "poland", "spain", "netherlands",
              "new york", "san francisco", "seattle", "california", "texas", "remote - emea", "emea", "apac"]


def is_canada(loc: str) -> bool:
    l = (loc or "").lower()
    return any(h in l for h in CANADA_HINTS) or bool(CANADA_PROVINCE.search(loc or ""))


def is_gta(loc: str) -> bool:
    l = (loc or "").lower()
    return any(h in l for h in GTA)


def is_remote(loc: str, flag: bool = False) -> bool:
    return flag or "remote" in (loc or "").lower()


def location_ok(loc: str, remote_flag: bool = False) -> bool:
    """Keep Canadian jobs, plus remote jobs that aren't tied to another country."""
    if is_canada(loc):
        return True
    if is_remote(loc, remote_flag):
        l = (loc or "").lower()
        return not any(has_term(l, n) if len(n) <= 3 else n in l for n in NON_CANADA)
    return False


# ---- normalization for dedupe ----
_COMPANY_SUFFIX = re.compile(r"\b(inc|incorporated|ltd|limited|llc|corp|corporation|co|company|plc|"
                             r"canada|group|holdings|technologies|technology|the)\b\.?")
_TITLE_NOISE = re.compile(r"\b(19|20)\d{2}\b|\b(winter|summer|fall|spring|autumn|january|may|september|"
                          r"jan|sept?|term|months?|\d+)\b")
# Name prefix -> one canonical name. A prefix matches whole words only ("td " not "tdx").
ALIASES = {"royal bank of": "rbc", "rbc": "rbc", "toronto dominion": "td", "td": "td",
           "bank of montreal": "bmo", "bmo": "bmo",
           "canadian imperial bank of commerce": "cibc", "cibc": "cibc",
           "bank of nova scotia": "scotiabank", "scotiabank": "scotiabank",
           "amazon web services": "amazon", "aws": "amazon", "meta platforms": "meta"}


def norm_company(name: str) -> str:
    n = (name or "").lower().replace("&", " and ")
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(r"^\s*the\s+", "", n)
    for k, v in ALIASES.items():
        if re.match(rf"\s*{re.escape(k)}\b", n):
            n = v
            break
    n = _COMPANY_SUFFIX.sub(" ", n)
    return _WS.sub(" ", n).strip()


def norm_title(title: str) -> str:
    t = (title or "").lower()
    t = re.sub(r"co[\s-]?op|internship|work term|placement", "intern", t)
    t = re.sub(r"\bsr\b|\bjr\b", "", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = _TITLE_NOISE.sub(" ", t)
    return _WS.sub(" ", t).strip()

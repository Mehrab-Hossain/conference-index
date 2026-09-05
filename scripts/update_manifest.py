"""
Refreshes the paper manifest and a separate official conference-information
feed. The latter is built only from venue/organization-owned web pages and
records its source and retrieval time for auditability.

The dashboard itself (index.html) never calls the GitHub API -- it only
reads manifest.json (same repo, no rate limit) and fetches the actual
paper XML from raw.githubusercontent.com (CDN-backed, effectively no
rate limit, CORS-enabled). This script's only job is to keep the *list*
of collection IDs in manifest.json up to date so the dashboard picks up
newly-added venues/years automatically.
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# Venue prefixes this dashboard tracks. Add to this list to track more
# ACL Anthology venues -- the script will then auto-discover all years
# for that venue going forward without further code changes.
TRACKED_VENUE_PREFIXES = [
    # Flagship ACL-family conferences and journals
    "acl", "emnlp", "naacl", "eacl", "findings", "coling", "aacl",
    "conll", "tacl", "cl", "anlp",
    # ACL Anthology's general "miscellaneous workshop" bucket (small,
    # but catches papers that don't have their own dedicated venue code)
    "ws",
    # Major non-ACL-operated venues the Anthology hosts
    "ijcnlp", "lrec", "amta", "eamt", "mtsummit", "nodalida", "ranlp",
    "paclic", "konvens", "ccl", "clicit", "ijclclp", "jeptalnrecital",
    "jlcl", "lilt", "tal", "alta", "hlt", "iwsds", "rocling", "nejlt",
    "muc", "tinlap", "tipster", "aimecon",
    # Shared-task / evaluation venues
    "wmt", "semeval", "starsem", "iwslt", "arabicnlp",
    # Well-established recurring workshops with their own venue codes
    # (verified against the live Anthology repo; harmless to list ones
    # that don't exist for a given year -- they're simply skipped)
    "sigdial", "inlg", "vardial", "wat", "wnut", "fever", "mrl",
    "blackboxnlp", "sigtyp", "sigmorphon", "signll", "loresmt",
    "americasnlp", "crac", "nlp4convai", "sustainlp", "codi",
    "textgraphs", "louhi", "clpsych", "repl4nlp", "nllp", "gem",
    "tsar", "socialnlp", "case", "insights", "sigul", "ltedi",
    "dravidianlangtech", "mwe", "argmining", "climatenlp", "trustnlp",
    "privatenlp", "gebnlp", "finnlp", "nlp4dh", "latechclfl", "nlrse",
    "genbench", "sdp", "cmcl", "law",
]

# Only track collections from this year onward, to keep the dashboard's
# scope (and the amount of data a visitor's browser has to fetch) bounded.
MIN_YEAR = datetime.now(timezone.utc).year - 9

COLLECTION_RE = re.compile(r"^(\d{4})\.([a-z0-9]+)$")

OFFICIAL_CONFERENCES = {
    "acl": {
        "name": "ACL", "edition": "ACL 2026",
        "source_url": "https://2026.aclweb.org/calls/main_conference_papers/",
        "homepage_url": "https://2026.aclweb.org/",
        "guidelines_url": "https://2026.aclweb.org/calls/main_conference_papers/",
    },
    "emnlp": {
        "name": "EMNLP", "edition": "EMNLP 2026",
        "source_url": "https://2026.emnlp.org/calls/main_conference_papers/",
        "homepage_url": "https://2026.emnlp.org/",
        "guidelines_url": "https://2026.emnlp.org/calls/main_conference_papers/",
    },
    "naacl": {
        "name": "NAACL", "edition": "NAACL 2027",
        "source_url": "https://www.aclweb.org/portal/naacl",
        "homepage_url": "https://www.aclweb.org/portal/naacl",
        "guidelines_url": "https://aclrollingreview.org/cfp",
        "announcement_pending": True,
    },
    "eacl": {
        "name": "EACL", "edition": "EACL 2027",
        "source_url": "https://2027.eacl.org/calls/papers/",
        "homepage_url": "https://2027.eacl.org/",
        "guidelines_url": "https://2027.eacl.org/calls/papers/",
    },
    "findings": {
        "name": "Findings", "edition": "Findings via ACL Rolling Review",
        "source_url": "https://aclrollingreview.org/cfp",
        "homepage_url": "https://aclrollingreview.org/",
        "guidelines_url": "https://aclrollingreview.org/cfp",
    },
    "coling": {
        "name": "COLING", "edition": "COLING 2027",
        "source_url": "https://2027.coling-iccl.org/calls/main_conference_papers/",
        "homepage_url": "https://2027.coling-iccl.org/",
        "guidelines_url": "https://2027.coling-iccl.org/calls/main_conference_papers/",
    },
    "conll": {
        "name": "CoNLL", "edition": "CoNLL 2026",
        "source_url": "https://conll.org/",
        "homepage_url": "https://conll.org/",
        "guidelines_url": "https://conll.org/",
    },
    "wmt": {
        "name": "WMT", "edition": "WMT 2026",
        "source_url": "https://www2.statmt.org/wmt26/",
        "homepage_url": "https://www2.statmt.org/wmt26/",
        "guidelines_url": "https://www2.statmt.org/wmt26/",
    },
    "semeval": {
        "name": "SemEval", "edition": "SemEval 2027",
        "source_url": "https://semeval.github.io/",
        "homepage_url": "https://semeval.github.io/",
        "guidelines_url": "https://semeval.github.io/paper-requirements.html",
    },
    "starsem": {
        "name": "*SEM", "edition": "*SEM 2026",
        "source_url": "https://starsem2026.github.io/calls/",
        "homepage_url": "https://starsem2026.github.io/",
        "guidelines_url": "https://starsem2026.github.io/calls/",
    },
    "tacl": {
        "name": "TACL", "edition": "TACL (rolling journal)",
        "source_url": "https://transacl.org/index.php/tacl/about/submissions",
        "homepage_url": "https://transacl.org/",
        "guidelines_url": "https://direct.mit.edu/tacl/pages/submission-guidelines",
    },
    "iwslt": {
        "name": "IWSLT", "edition": "IWSLT 2027",
        "source_url": "https://iwslt.org/",
        "homepage_url": "https://iwslt.org/",
        "guidelines_url": "https://iwslt.org/faq/",
    },
    "lrec": {
        "name": "LREC", "edition": "LREC 2026",
        "source_url": "https://lrec2026.info/calls/",
        "homepage_url": "https://lrec2026.info/",
        "guidelines_url": "https://lrec2026.info/authors-kit/",
    },
    "arabicnlp": {
        "name": "ArabicNLP", "edition": "ArabicNLP 2026",
        "source_url": "https://arabicnlp2026.sigarab.org/call-for-papers",
        "homepage_url": "https://arabicnlp2026.sigarab.org/",
        "guidelines_url": "https://arabicnlp2026.sigarab.org/call-for-papers",
    },
    "ijcnlp": {
        "name": "AACL-IJCNLP", "edition": "AACL-IJCNLP 2026",
        "source_url": "https://2026.aaclnet.org/calls/main_conference_papers/",
        "homepage_url": "https://2026.aaclnet.org/",
        "guidelines_url": "https://2026.aaclnet.org/calls/main_conference_papers/",
    },
}

DATE_WORDS = re.compile(
    r"deadline|due|notification|camera.?ready|conference|commitment|response|review|submission|evaluation",
    re.I,
)
DATE_VALUES = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?\s+\d{1,2}|\bTBA\b|\brolling\b",
    re.I,
)
TOPIC_HEADING = re.compile(r"topics?|areas? (?:of interest|covered)|scope", re.I)
GUIDELINE_WORDS = re.compile(
    r"\bpage limit\b|\b\d+\s*(?:content\s+)?pages?\b|\banonym(?:ity|ous|ized)\b|"
    r"\btemplate\b|\bformat(?:ting)?\b|\bsupplementary\b|\breview process\b|"
    r"\bsubmission (?:must|should)\b|\boriginal and unpublished\b",
    re.I,
)


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def unique_limited(values, limit, max_length=500):
    output = []
    seen = set()
    for value in values:
        value = clean_text(value)
        key = value.casefold()
        if not value or len(value) > max_length or key in seen:
            continue
        seen.add(key)
        output.append(value)
        if len(output) >= limit:
            break
    return output


def extract_official_page(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select("script, style, nav, footer, form"):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup

    dates = []
    for row in root.select("tr"):
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.select("th, td")]
        line = " — ".join(cell for cell in cells if cell)
        if DATE_WORDS.search(line) and DATE_VALUES.search(line):
            dates.append(line)
    for item in root.select("li, p"):
        line = clean_text(item.get_text(" ", strip=True))
        if DATE_WORDS.search(line) and DATE_VALUES.search(line) and len(line) <= 240:
            dates.append(line)

    topics = []
    for heading in root.find_all(re.compile(r"^h[1-6]$")):
        if not TOPIC_HEADING.search(clean_text(heading.get_text(" ", strip=True))):
            continue
        for element in heading.find_all_next(["h2", "h3", "h4", "ul", "ol"]):
            if element.name in {"h2", "h3", "h4"}:
                break
            if element.name in {"ul", "ol"}:
                topics.extend(clean_text(li.get_text(" ", strip=True)) for li in element.find_all("li", recursive=False))
                break
        if topics:
            break

    guidelines = []
    for item in root.select("p, li"):
        line = clean_text(item.get_text(" ", strip=True))
        if GUIDELINE_WORDS.search(line) and 25 <= len(line) <= 500:
            guidelines.append(line)

    title = clean_text((root.find("h1") or soup.title or root).get_text(" ", strip=True))
    return {
        "page_title": title[:180],
        "dates": unique_limited(dates, 14, 240),
        "topics": unique_limited(topics, 30, 180),
        "guidelines": unique_limited(guidelines, 8, 500),
    }


def refresh_conference_info(output_path):
    previous = {}
    if os.path.exists(output_path):
        try:
            with open(output_path, encoding="utf-8") as handle:
                previous = json.load(handle).get("venues", {})
        except (OSError, ValueError):
            pass

    checked_at = datetime.now(timezone.utc).isoformat()
    venues = {}
    headers = {"User-Agent": "conference-index/1.0 (+GitHub Pages research dashboard)"}

    def refresh_one(code, config):
        record = {**config, "checked_at": checked_at, "source_status": "unavailable"}
        try:
            response = requests.get(config["source_url"], headers=headers, timeout=30)
            response.raise_for_status()
            record.update(extract_official_page(response.text))
            record["source_status"] = "official page checked"
            if config.get("announcement_pending"):
                record["status_note"] = "The dedicated call for papers has not been announced on an official conference site."
        except Exception as error:
            old = previous.get(code, {})
            for key in ("page_title", "dates", "topics", "guidelines"):
                if old.get(key):
                    record[key] = old[key]
            record["status_note"] = f"Official source could not be refreshed; showing the last available record. ({type(error).__name__})"
        return code, record

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(refresh_one, code, config) for code, config in OFFICIAL_CONFERENCES.items()]
        for future in as_completed(futures):
            code, record = future.result()
            venues[code] = record
            print(f"Official info: {code}: {record['source_status']}")

    venues = {code: venues[code] for code in OFFICIAL_CONFERENCES}

    payload = {
        "generated_at": checked_at,
        "method": "Daily extraction from official venue or sponsoring-organization pages",
        "venues": venues,
    }
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


# --------------------------------------------------------------------
# ACM/ML venues that aren't on the ACL Anthology (no CORS-friendly raw
# data source exists for them), scraped from DBLP instead. DBLP does
# NOT send CORS headers, so this can only run here -- server-side, in
# the GitHub Action -- never as a live client-side fetch. The output is
# a plain same-origin JSON file the dashboard reads like any other file
# in the repo, exactly like conference-info.json.
#
# NOTE ON CONFIDENCE: this scraper's HTML-parsing assumptions (DBLP's
# `li.entry` / `span.title` structure) are based on DBLP's long-standing,
# well-documented page layout, but have not been exercised against a
# live fetch during development (network restrictions in the dev
# environment). Run the workflow once manually (Actions tab ->
# "Refresh venue manifest" -> "Run workflow") and check its logs / the
# resulting acm-papers.json before trusting this in production. If a
# venue silently returns 0 papers, DBLP likely restructured that page.
# --------------------------------------------------------------------

DBLP_VENUES = {
    "chi": {"name": "ACM CHI", "dblp_key": "chi", "years": None},
    "iui": {"name": "ACM IUI", "dblp_key": "iui", "years": None},
    "cscw": {"name": "ACM CSCW", "dblp_key": "cscw", "years": None},
    "hcomp": {"name": "AAAI HCOMP", "dblp_key": "hcomp", "years": None},
    "facct": {"name": "ACM FAccT", "dblp_key": "fat", "years": None},
    "aies": {"name": "AIES", "dblp_key": "aies", "years": None},
    "aamas": {"name": "AAMAS", "dblp_key": "atal", "years": None},
    "neurips": {"name": "NeurIPS", "dblp_key": "nips", "years": None},
    "icml": {"name": "ICML", "dblp_key": "icml", "years": None},
    "iclr": {"name": "ICLR", "dblp_key": "iclr", "years": None},
    "aaai": {"name": "AAAI", "dblp_key": "aaai", "years": None},
    "ijcai": {"name": "IJCAI", "dblp_key": "ijcai", "years": None},
}

# How many recent years to try per venue when `years` isn't set above.
# Kept short deliberately: each year is a separate DBLP page fetch, and
# DBLP is a shared community resource -- be a polite, low-volume client.
DBLP_RECENT_YEARS = 3


def fetch_dblp_venue_year(venue_code, dblp_key, year, headers):
    """Fetch and parse one year of one DBLP-indexed venue. Returns a list
    of paper records in the same shape as ACL Anthology records, or an
    empty list if the page doesn't exist / doesn't parse (never raises
    for a routine 404 -- a venue simply not having papers for a given
    year is normal, not an error).

    `venue_code` is our internal short code (e.g. "aamas") used for
    display/filtering; `dblp_key` is DBLP's own series key for that venue
    (e.g. "atal" for AAMAS) used only to build the fetch URL -- these can
    differ, so don't conflate them in the output record's `v` field.

    Returns (records, outcome) where outcome is one of:
      "ok"            -- fetched and parsed successfully (records may
                          still be empty if the page had no entries)
      "not_found"     -- DBLP returned 404; this venue/year combo just
                          doesn't exist, which is normal and expected
      "rate_limited"  -- DBLP returned 429 (their documented behavior
                          for excessive request volume, per their FAQ)
      "unreachable"   -- connection failed outright (timeout / refused /
                          DNS failure) -- this is the pattern consistent
                          with a network-level block of the runner's IP
                          range, NOT DBLP's documented rate-limiting
                          (which returns 429, not a dropped connection)
      "server_error"  -- DBLP responded, but with a 5xx (e.g. 503) even
                          after retries. Unlike "unreachable", the
                          connection itself worked -- this could be a
                          real (possibly transient) problem on DBLP's
                          end, or a soft anti-bot response on that
                          specific path. Doesn't count toward the
                          network-block diagnostic below, since the
                          network path clearly isn't blocked.
    """
    url = f"https://dblp.org/db/conf/{dblp_key}/{dblp_key}{year}.html"
    max_attempts = 3
    last_error = None
    # Longer, more generous backoff than a typical transient-error retry.
    # Evidence from real runs (see git history / conversation with the
    # maintainer around 2026-09-05): venues processed early in a run
    # would fail with 503 while the last venue processed (after several
    # minutes of accumulated delay from earlier retries) succeeded
    # cleanly. That pattern -- failures cluster early, success appears
    # once enough wall-clock time has passed -- looks like a rolling
    # rate-limit window, not a blanket IP block. A short 2s/4s backoff
    # just re-hits the same still-active window; waiting longer gives it
    # an actual chance to clear.
    retry_delays = [15, 45]  # seconds, for attempts 1 and 2

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(url, headers=headers, timeout=20)
        except requests.exceptions.RequestException as error:
            last_error = error
            if attempt < max_attempts:
                time.sleep(retry_delays[attempt - 1])
                continue
            print(f"  UNREACHABLE: {venue_code} ({dblp_key}) {year} after {max_attempts} attempts: {error}", file=sys.stderr)
            return [], "unreachable"

        if response.status_code == 404:
            return [], "not_found"
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            print(f"  RATE LIMITED: {venue_code} ({dblp_key}) {year} -- DBLP asked us to wait {retry_after or 'some time'}", file=sys.stderr)
            return [], "rate_limited"
        if response.status_code >= 500:
            # Server-side errors (503 Service Unavailable, etc.) are often
            # transient -- retry with backoff same as a connection failure,
            # but track separately: a real HTTP response (even an error
            # one) means the connection itself is fine, so this should
            # NOT count toward the "network block" diagnostic below.
            if attempt < max_attempts:
                print(f"  Server error {response.status_code} for {venue_code} ({dblp_key}) {year}, waiting {retry_delays[attempt-1]}s before retrying...", file=sys.stderr)
                time.sleep(retry_delays[attempt - 1])
                continue
            print(f"  SERVER ERROR: {venue_code} ({dblp_key}) {year} still {response.status_code} after {max_attempts} attempts", file=sys.stderr)
            return [], "server_error"
        try:
            response.raise_for_status()
        except Exception as error:
            print(f"  DBLP returned an error for {venue_code} ({dblp_key}) {year}: {error}", file=sys.stderr)
            return [], "error"
        break  # success

    soup = BeautifulSoup(response.text, "html.parser")
    records = []
    for entry in soup.select("li.entry"):
        title_span = entry.select_one("span.title")
        if title_span is None:
            continue
        title = clean_text(title_span.get_text(" ", strip=True)).rstrip(".")
        if not title:
            continue
        authors = [
            clean_text(a.get_text(" ", strip=True))
            for a in entry.select('span[itemprop="author"] span[itemprop="name"]')
        ]
        a_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")

        link = url
        ee_link = entry.select_one("li.ee a")
        if ee_link and ee_link.get("href"):
            link = ee_link["href"]
        else:
            rec_link = entry.select_one('a[href^="https://dblp.org/rec/"]')
            if rec_link and rec_link.get("href"):
                link = rec_link["href"]

        records.append({
            "t": title,
            "a": a_str,
            "v": venue_code,
            "tr": "paper",
            "y": str(year),
            "u": link,
        })
    return records, "ok"


def refresh_dblp_papers(output_path):
    previous_records = []
    if os.path.exists(output_path):
        try:
            with open(output_path, encoding="utf-8") as handle:
                previous_records = json.load(handle).get("papers", [])
        except (OSError, ValueError):
            pass
    previous_by_venue = {}
    for record in previous_records:
        previous_by_venue.setdefault(record.get("v"), []).append(record)

    checked_at = datetime.now(timezone.utc).isoformat()
    # DBLP's own fair-use policy (dblp1.uni-trier.de/faq) asks for an
    # identifiable User-Agent with a contact/reference URL, and at least
    # one second between requests -- both followed here.
    headers = {"User-Agent": "conference-index-bot/1.0 (+https://github.com/Mehrab-Hossain/conference-index)"}
    current_year = datetime.now(timezone.utc).year
    years_to_try = list(range(current_year, current_year - DBLP_RECENT_YEARS, -1))

    all_records = []
    outcome_counts = {}
    for code, config in DBLP_VENUES.items():
        years = config["years"] or years_to_try
        venue_records = []
        for year in years:
            records, outcome = fetch_dblp_venue_year(code, config["dblp_key"], year, headers)
            venue_records.extend(records)
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
            time.sleep(4)  # stay well under DBLP's fair-use rate limit -- see
            # note on retry_delays above; a rolling rate-limit window is the
            # leading theory, so a more generous steady-state gap (not just
            # longer retries) reduces the chance of triggering it at all
        if venue_records:
            print(f"DBLP: {code}: {len(venue_records)} papers across {years}")
            all_records.extend(venue_records)
        else:
            fallback = previous_by_venue.get(code, [])
            print(f"DBLP: {code}: 0 papers fetched -- keeping {len(fallback)} from last successful run")
            all_records.extend(fallback)

    # Diagnostic verdict: if a large share of requests came back
    # "unreachable" (dropped connection) or "server_error" (persistent
    # 5xx after retries) rather than a mix of "ok" and "not_found"
    # (normal -- not every venue publishes every year), that's the
    # signature of DBLP (or a WAF in front of it) blocking this
    # runner's traffic, not a data problem. Both failure modes have
    # been observed in practice for this exact setup: a hard connection
    # timeout on one run, a persistent 503 across multiple *different*,
    # definitely-correctly-spelled venues on another. Treat them as the
    # same underlying problem rather than debugging each venue in
    # isolation -- see the module docstring for what to do about it.
    total_checks = sum(outcome_counts.values())
    blocked_like = outcome_counts.get("unreachable", 0) + outcome_counts.get("server_error", 0)
    if total_checks and blocked_like / total_checks > 0.3:
        print(
            f"\nWARNING: {blocked_like}/{total_checks} DBLP requests failed with a "
            "connection timeout or a persistent 5xx error (not a normal 404). When "
            "this pattern spans multiple different, correctly-spelled venues, it "
            "means DBLP (or a WAF in front of it) is blocking or soft-blocking this "
            "runner's traffic -- not that the requested pages don't exist. Retrying "
            "harder from the same network won't fix this; the only paths forward are "
            "(1) run this script from a non-datacenter network (e.g. your own machine) "
            "and commit the result manually, or (2) accept that ACM/ML paper coverage "
            "stays at whatever was last fetched successfully, while the ACL Anthology "
            "side (a different data source entirely) keeps updating normally. "
            "Falling back to last-known-good data for affected venues.",
            file=sys.stderr,
        )

    payload = {
        "generated_at": checked_at,
        "method": "Scraped server-side from DBLP (dblp.org) by the GitHub Action -- "
                  "not fetched live by visitors' browsers, since DBLP doesn't allow "
                  "cross-origin browser requests.",
        "papers": all_records,
    }
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def list_repo_xml_files():
    """List every file in acl-org/acl-anthology's data/xml directory via
    the GitHub Trees API (one call, not one call per file)."""
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = "https://api.github.com/repos/acl-org/acl-anthology/git/trees/master?recursive=1"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    tree = resp.json()["tree"]

    xml_files = [
        item["path"] for item in tree
        if item["path"].startswith("data/xml/") and item["path"].endswith(".xml")
    ]
    return xml_files


def matching_collections(xml_paths):
    collections = []
    for path in xml_paths:
        stem = path[len("data/xml/"):-len(".xml")]
        m = COLLECTION_RE.match(stem)
        if not m:
            continue
        year, venue = int(m.group(1)), m.group(2)
        if year >= MIN_YEAR and venue in TRACKED_VENUE_PREFIXES:
            collections.append(stem)
    return sorted(collections)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir) if os.path.basename(script_dir) == "scripts" else script_dir
    manifest_path = os.path.join(repo_root, "manifest.json")
    conference_info_path = os.path.join(repo_root, "conference-info.json")
    acm_papers_path = os.path.join(repo_root, "acm-papers.json")

    try:
        xml_paths = list_repo_xml_files()
    except Exception as e:
        print(f"Failed to list ACL Anthology repo contents: {e}", file=sys.stderr)
        sys.exit(1)

    collections = matching_collections(xml_paths)

    if not collections:
        print("No matching collections found -- refusing to write an empty manifest.", file=sys.stderr)
        sys.exit(1)

    manifest = {
        "collections": collections,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "https://github.com/acl-org/acl-anthology/tree/master/data/xml",
        "tracked_venue_prefixes": TRACKED_VENUE_PREFIXES,
        "min_year": MIN_YEAR,
    }

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    print(f"Wrote {len(collections)} collections to manifest.json")
    refresh_conference_info(conference_info_path)
    print("Wrote official conference information to conference-info.json")
    refresh_dblp_papers(acm_papers_path)
    print("Wrote DBLP-sourced ACM/ML papers to acm-papers.json")


if __name__ == "__main__":
    main()

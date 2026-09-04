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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# Venue prefixes this dashboard tracks. Add to this list to track more
# ACL Anthology venues -- the script will then auto-discover all years
# for that venue going forward without further code changes.
TRACKED_VENUE_PREFIXES = [
    "acl", "emnlp", "naacl", "eacl", "findings", "coling", "conll",
    "wmt", "semeval", "starsem", "tacl", "iwslt", "lrec", "arabicnlp",
    "ijcnlp",
]

# Only track collections from this year onward, to keep the dashboard's
# scope (and the amount of data a visitor's browser has to fetch) bounded.
MIN_YEAR = 2020

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


if __name__ == "__main__":
    main()

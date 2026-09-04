"""
Refreshes manifest.json by asking GitHub which ACL Anthology collection
files currently exist, and keeping only the venues/years this dashboard
tracks. Run by the GitHub Actions workflow on a schedule -- not meant to
be run in a browser (needs a GITHUB_TOKEN for a decent API rate limit).

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
from datetime import datetime, timezone

import requests

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
MIN_YEAR = 2024

COLLECTION_RE = re.compile(r"^(\d{4})\.([a-z0-9]+)$")


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
    manifest_path = os.path.join(os.path.dirname(__file__), "..", "manifest.json")

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


if __name__ == "__main__":
    main()

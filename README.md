# Conference Index — live, free, public dashboard

A searchable index of accepted papers across NLP conferences (ACL, EMNLP,
NAACL, EACL, COLING, AACL-IJCNLP, CoNLL, WMT, SemEval, *SEM, TACL, IWSLT,
LREC, ArabicNLP), plus a reference section of submission info for
NeurIPS, ICML, ICLR, AAAI, IJCAI, ACM CHI, ACM IUI, AAMAS, ACM CSCW,
ACM HCOMP, ACM FAccT, AIES, MLHC, CHIL, and AMIA.

**This version fetches paper data live from the ACL Anthology's GitHub
repo, in the visitor's own browser, every time the page loads.** There's
no backend, no database, and no server cost — it's three static files.

## How it works

1. `index.html` loads `manifest.json` (same repo — instant, no rate limit).
2. For each venue listed in the manifest, it fetches that venue's raw
   XML directly from `raw.githubusercontent.com` (GitHub's CDN, which
   allows cross-origin browser requests — verified: it sends
   `access-control-allow-origin: *`) and parses it client-side with the
   browser's built-in `DOMParser`. No library, no API key, no backend.
3. Results are cached in the visitor's browser for 6 hours (`localStorage`)
   so repeat visits are fast, but there's also a **"Refresh now" button**
   that forces an immediate re-fetch, bypassing the cache.
4. A scheduled GitHub Action (`.github/workflows/update-manifest.yml`)
   runs once a day, asks GitHub which collection files currently exist
   in the ACL Anthology repo, and updates `manifest.json` if new
   venues/years have appeared. This is the only part that needs a
   server at all, and GitHub Actions provides it for free
   (2,000 free minutes/month on a free GitHub account — this job takes
   a few seconds, so you'd need roughly 100,000 runs/month before that
   became a concern).

Net effect: paper data is genuinely live (fetched fresh, or from a
≤6-hour cache, on every visit), and the *list* of venues to track stays
current automatically with at most a 24-hour lag for brand-new venues.

## What's NOT live (and can't easily be)

The "More conferences" section at the bottom (NeurIPS, ICML, ICLR, AAAI,
IJCAI, CHI, IUI, AAMAS, CSCW, HCOMP, FAccT, AIES, MLHC, CHIL, AMIA) is
still static data hardcoded in `index.html`. Those venues don't publish
their submission deadlines or paper lists anywhere with a stable,
CORS-enabled, machine-readable format the way the ACL Anthology does —
getting live data for them would mean either standing up a real backend
to scrape each site server-side (defeats the "free, no backend" goal)
or manually refreshing that section periodically. If you want to
automate part of this later, the cleanest targets are OpenReview's API
(covers ICLR, NeurIPS, ICML, FAccT — but needs a server-side proxy,
since `api.openreview.net` doesn't set CORS headers) and DBLP (works
similarly to the Anthology in structure, but also has no CORS headers).

## Deploying this for free with GitHub Pages

You need a GitHub account (free). Then:

1. **Create a new repository** on GitHub (public). Name it whatever you
   like, e.g. `conference-index`.

2. **Upload these four things** to the repo, preserving the folder
   structure:
   - `index.html`
   - `manifest.json`
   - `.github/workflows/update-manifest.yml`
   - `scripts/update_manifest.py`

   Easiest way: on the repo's GitHub page, use "Add file → Upload
   files" and drag in `index.html` and `manifest.json` at the root.
   For the `.github/workflows/` and `scripts/` folders, GitHub's web
   uploader preserves folder structure if you drag the whole folder,
   or you can create the files individually via "Add file → Create new
   file" and type the path (e.g. `scripts/update_manifest.py`) into the
   filename box — GitHub creates the folder automatically.

3. **Enable GitHub Pages**: repo Settings → Pages → under "Build and
   deployment", set Source to "Deploy from a branch", branch `main`,
   folder `/ (root)`. Save.

4. Wait a minute or two, then your site is live at:
   `https://<your-username>.github.io/<repo-name>/`

5. **Enable the scheduled workflow**: it should already be active once
   the `.github/workflows/update-manifest.yml` file is in the repo —
   GitHub Actions picks up scheduled workflows automatically. You can
   confirm it's there and trigger it manually under the repo's
   "Actions" tab (look for "Refresh venue manifest" → "Run workflow").

That's it — no billing information required anywhere in this flow, and
nothing here approaches any GitHub free-tier limit for a project this
size.

## Updating the tracked venues yourself

To add or remove venues, edit `TRACKED_VENUE_PREFIXES` and `MIN_YEAR` in
`scripts/update_manifest.py`, commit the change, and either wait for the
next scheduled run or trigger the workflow manually. The dashboard
itself needs no changes — it just reads whatever `manifest.json` says.

## Local testing before you deploy

You can preview the site locally without any of the GitHub setup:

```bash
python3 -m http.server 8000
```

then open `http://localhost:8000/index.html`. It'll fetch real, live
data from GitHub exactly as it would once deployed.

# Article Scraper

A Python crawler that starts at a URL, follows internal links across the
same domain, and extracts article text + metadata (title, author, date)
from each page it visits.

## Setup

```bash
pip install requests beautifulsoup4
pip install trafilatura   # optional, but improves extraction quality
```

## Usage

```bash
python article_scraper.py --url https://example.com --max-pages 50 --output articles.json
```

Options:

| Flag | Default | Description |
|---|---|---|
| `--url` | (required) | Starting URL to crawl |
| `--max-pages` | 50 | Max number of pages to visit |
| `--delay` | 1.0 | Seconds to wait between requests |
| `--min-text-length` | 300 | Minimum text length to count as an "article" |
| `--output` | articles.json | Output JSON file |
| `--csv` | (none) | Optional CSV output path |
| `--quiet` | off | Suppress progress logging |

## Example

```bash
python article_scraper.py --url https://blog.example.com --max-pages 100 --delay 1.5 --csv articles.csv
```

## Notes on responsible use

- The script checks `robots.txt` before fetching each page and will skip
  disallowed URLs. It also honors any `Crawl-delay` directive.
- Default delay is 1 second between requests — raise `--delay` for
  smaller/slower sites, especially ones without a CDN.
- Only crawls links on the same domain as the starting URL.
- Results save incrementally, so a long crawl won't lose progress if
  interrupted.
- Some sites' Terms of Service prohibit automated scraping regardless of
  robots.txt — worth checking before crawling a site you don't own or
  have permission to scrape at scale.

## How extraction works

If `trafilatura` is installed, it's used for extraction (generally more
accurate — handles boilerplate removal, metadata parsing, etc. well).
Otherwise the script falls back to a built-in heuristic: it removes nav/
footer/script tags, then picks whichever remaining block of the page has
the most paragraph text — a simple but effective way to find the "main
content" on most article/blog pages without extra dependencies.

---

## Running it as an API

`api.py` wraps the scraper in a FastAPI service with two endpoints:

- `POST /scrape` — scrape one URL, returns one article
- `POST /crawl` — crawl a site starting at a URL, returns a list of articles

### Run locally

```bash
pip install -r requirements.txt
uvicorn api:app --host 0.0.0.0 --port 8000
```

Visit `http://localhost:8000/docs` for interactive docs, and
`http://localhost:8000/openapi.json` for the live-generated OpenAPI spec.
A hand-written version is also included as `openapi.yaml` — either works
for the "Import API Source" step.

### Deploying (so it has a public URL)

Easiest free/cheap options for a first deploy:

**Render.com**
1. Push this folder to a GitHub repo.
2. New → Web Service → connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn api:app --host 0.0.0.0 --port $PORT`
5. Deploy — Render gives you a public URL like `https://your-app.onrender.com`.

**Railway.app** — same idea: connect the repo, it auto-detects Python,
set the start command to the uvicorn line above.

**Fly.io** — good if you want more control; needs a `Dockerfile`, happy
to add one if you go this route.

Once deployed, update the `servers.url` field at the top of `openapi.yaml`
with your real URL, then paste that file into api.market's "Import API
Source" step.

### A note on scale

`/crawl` runs synchronously in this version — fine for testing and small
crawls, but a `max_pages=200` request could take minutes and tie up the
web request. For production use, this is the kind of endpoint you'd
eventually want to run as a background job (e.g. with Celery or a task
queue) that a client polls for status — happy to build that version if
you get to that point.

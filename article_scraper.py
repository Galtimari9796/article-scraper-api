#!/usr/bin/env python3
"""
Article Scraper — crawls a website and extracts article text + metadata.

Usage:
    python article_scraper.py --url https://example.com --max-pages 50 --output articles.json

Features:
    - Breadth-first crawl restricted to the starting domain
    - Respects robots.txt
    - Configurable delay between requests (be polite!)
    - Extracts title, author, publish date, and main article text
    - Uses trafilatura if installed (better extraction), otherwise falls
      back to a built-in heuristic extractor based on BeautifulSoup
    - Saves results incrementally to JSON (and optionally CSV)
    - Skips non-HTML content, dedupes URLs, handles retries/timeouts

Install dependencies:
    pip install requests beautifulsoup4
    pip install trafilatura   # optional, improves extraction quality
"""

import argparse
import csv
import json
import re
import sys
import time
import urllib.robotparser
from collections import deque
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

USER_AGENT = "ArticleScraperBot/1.0 (+https://example.com/bot-info)"


# --------------------------------------------------------------------------
# Robots.txt handling
# --------------------------------------------------------------------------

class RobotsChecker:
    def __init__(self, base_url, user_agent=USER_AGENT):
        self.user_agent = user_agent
        parsed = urlparse(base_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        self.rp = urllib.robotparser.RobotFileParser()
        self.rp.set_url(robots_url)
        try:
            self.rp.read()
        except Exception:
            # If robots.txt can't be fetched, default to allowing crawl
            self.rp = None

    def can_fetch(self, url):
        if self.rp is None:
            return True
        try:
            return self.rp.can_fetch(self.user_agent, url)
        except Exception:
            return True

    def crawl_delay(self):
        if self.rp is None:
            return None
        try:
            return self.rp.crawl_delay(self.user_agent)
        except Exception:
            return None


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def extract_with_trafilatura(html, url):
    result = trafilatura.extract(
        html, url=url, include_comments=False, include_tables=False,
        with_metadata=True, output_format="json"
    )
    if not result:
        return None
    data = json.loads(result)
    return {
        "title": data.get("title"),
        "author": data.get("author"),
        "date": data.get("date"),
        "text": data.get("text"),
        "excerpt": (data.get("text") or "")[:300],
    }


def extract_with_bs4(html, url):
    """Fallback heuristic extractor: picks the densest text block on the page."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()

    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()

    author = None
    for sel in [
        {"name": "author"}, {"property": "article:author"},
        {"name": "byl"}, {"itemprop": "author"},
    ]:
        m = soup.find("meta", attrs=sel)
        if m and m.get("content"):
            author = m["content"].strip()
            break

    date = None
    for sel in [
        {"property": "article:published_time"}, {"name": "date"},
        {"itemprop": "datePublished"}, {"name": "publish-date"},
    ]:
        m = soup.find("meta", attrs=sel)
        if m and m.get("content"):
            date = m["content"].strip()
            break

    candidates = soup.find_all(["article", "main", "div", "section"])
    best_text, best_len = "", 0
    for c in candidates:
        paragraphs = c.find_all("p", recursive=True)
        text = "\n".join(p.get_text(" ", strip=True) for p in paragraphs)
        if len(text) > best_len:
            best_text, best_len = text, len(text)

    if best_len < 200:
        paragraphs = soup.find_all("p")
        best_text = "\n".join(p.get_text(" ", strip=True) for p in paragraphs)

    if not best_text:
        return None

    return {
        "title": title,
        "author": author,
        "date": date,
        "text": best_text,
        "excerpt": best_text[:300],
    }


def extract_article(html, url):
    if HAS_TRAFILATURA:
        result = extract_with_trafilatura(html, url)
        if result and result.get("text"):
            return result
    return extract_with_bs4(html, url)


# --------------------------------------------------------------------------
# Crawler
# --------------------------------------------------------------------------

def is_same_domain(url, domain):
    return urlparse(url).netloc == domain


def normalize_url(url):
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


SKIP_EXTENSIONS = re.compile(
    r"\.(jpg|jpeg|png|gif|svg|pdf|zip|mp4|mp3|css|js|ico|woff|woff2|ttf|xml)$",
    re.IGNORECASE,
)


def crawl(
    start_url,
    max_pages=50,
    delay=1.0,
    min_text_length=300,
    output_path="articles.json",
    csv_path=None,
    verbose=True,
):
    domain = urlparse(start_url).netloc
    robots = RobotsChecker(start_url)
    effective_delay = max(delay, robots.crawl_delay() or 0)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    visited = set()
    queue = deque([start_url])
    results = []

    while queue and len(visited) < max_pages:
        url = normalize_url(queue.popleft())
        if url in visited:
            continue
        visited.add(url)

        if SKIP_EXTENSIONS.search(url):
            continue

        if not robots.can_fetch(url):
            if verbose:
                print(f"[skip: robots.txt] {url}")
            continue

        try:
            resp = session.get(url, timeout=10)
            content_type = resp.headers.get("Content-Type", "")
            if resp.status_code != 200 or "text/html" not in content_type:
                if verbose:
                    print(f"[skip: {resp.status_code} {content_type}] {url}")
                time.sleep(effective_delay)
                continue
        except requests.RequestException as e:
            if verbose:
                print(f"[error] {url}: {e}")
            time.sleep(effective_delay)
            continue

        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # Queue internal links for further crawling
        for a in soup.find_all("a", href=True):
            link = normalize_url(urljoin(url, a["href"]))
            if is_same_domain(link, domain) and link not in visited:
                queue.append(link)

        # Try to extract article content from this page
        article = extract_article(html, url)
        if article and article.get("text") and len(article["text"]) >= min_text_length:
            article["url"] = url
            article["scraped_at"] = datetime.utcnow().isoformat() + "Z"
            results.append(article)
            if verbose:
                print(f"[article] {url}  ({len(article['text'])} chars)")
            # Save incrementally so you don't lose progress on a long crawl
            save_json(results, output_path)
            if csv_path:
                save_csv(results, csv_path)
        elif verbose:
            print(f"[page, not article] {url}")

        time.sleep(effective_delay)

    return results


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def save_json(results, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def save_csv(results, path):
    if not results:
        return
    fields = ["url", "title", "author", "date", "scraped_at", "excerpt", "text"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k, "") for k in fields})


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Crawl a site and extract article content.")
    parser.add_argument("--url", required=True, help="Starting URL (e.g. https://example.com)")
    parser.add_argument("--max-pages", type=int, default=50, help="Max pages to visit")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between requests")
    parser.add_argument("--min-text-length", type=int, default=300,
                         help="Minimum extracted text length to count as an 'article'")
    parser.add_argument("--output", default="articles.json", help="Output JSON path")
    parser.add_argument("--csv", default=None, help="Optional output CSV path")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress logging")
    args = parser.parse_args()

    if not HAS_TRAFILATURA:
        print("Note: trafilatura not installed — using built-in fallback extractor.")
        print("For better extraction quality: pip install trafilatura\n")

    results = crawl(
        start_url=args.url,
        max_pages=args.max_pages,
        delay=args.delay,
        min_text_length=args.min_text_length,
        output_path=args.output,
        csv_path=args.csv,
        verbose=not args.quiet,
    )

    print(f"\nDone. Extracted {len(results)} articles from {args.url}")
    print(f"Saved to {args.output}" + (f" and {args.csv}" if args.csv else ""))


if __name__ == "__main__":
    main()

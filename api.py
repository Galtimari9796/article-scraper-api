#!/usr/bin/env python3
"""
Article Scraper API — a small FastAPI service wrapping article_scraper.py.

Endpoints:
    POST /scrape        Scrape a single URL, return extracted article content
    POST /crawl         Crawl a site starting at a URL, return multiple articles
    GET  /health         Simple healthcheck

Run locally:
    pip install fastapi uvicorn requests beautifulsoup4
    pip install trafilatura   # optional, improves extraction quality
    uvicorn api:app --host 0.0.0.0 --port 8000

Then visit http://localhost:8000/docs for interactive API docs, and
http://localhost:8000/openapi.json for the raw OpenAPI spec (this is the
file you can paste into api.market's "Import API Source" step).
"""

from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from article_scraper import (
    USER_AGENT,
    RobotsChecker,
    crawl as crawl_site,
    extract_article,
    normalize_url,
)

app = FastAPI(
    title="Article Scraper API",
    description="Extracts clean article text and metadata (title, author, date) from a URL, or crawls a site to collect multiple articles.",
    version="1.0.0",
)


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

class ScrapeRequest(BaseModel):
    url: str = Field(..., description="The URL of the article to scrape", example="https://example.com/blog/post-1")


class Article(BaseModel):
    url: str
    title: Optional[str] = None
    author: Optional[str] = None
    date: Optional[str] = None
    text: str
    excerpt: str


class ScrapeResponse(BaseModel):
    article: Article


class CrawlRequest(BaseModel):
    url: str = Field(..., description="Starting URL to crawl", example="https://example.com")
    max_pages: int = Field(20, ge=1, le=200, description="Maximum number of pages to visit")
    delay: float = Field(1.0, ge=0, description="Seconds to wait between requests")
    min_text_length: int = Field(300, ge=0, description="Minimum extracted text length to count as an article")


class CrawlResponse(BaseModel):
    count: int
    articles: List[Article]


class ErrorResponse(BaseModel):
    detail: str


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post(
    "/scrape",
    response_model=ScrapeResponse,
    responses={400: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
def scrape(req: ScrapeRequest):
    url = normalize_url(req.url)

    robots = RobotsChecker(url)
    if not robots.can_fetch(url):
        raise HTTPException(status_code=403, detail="Blocked by the site's robots.txt")

    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"URL returned status {resp.status_code}")

    article = extract_article(resp.text, url)
    if not article or not article.get("text"):
        raise HTTPException(status_code=400, detail="Could not extract article content from this page")

    article["url"] = url
    return {"article": article}


@app.post(
    "/crawl",
    response_model=CrawlResponse,
    responses={400: {"model": ErrorResponse}},
)
def crawl(req: CrawlRequest):
    try:
        results = crawl_site(
            start_url=req.url,
            max_pages=req.max_pages,
            delay=req.delay,
            min_text_length=req.min_text_length,
            output_path="/tmp/_crawl_output.json",
            csv_path=None,
            verbose=False,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Crawl failed: {e}")

    return {"count": len(results), "articles": results}

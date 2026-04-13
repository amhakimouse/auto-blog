"""
app/scrapers/__init__.py — Scraper modules (isolated, swappable).
"""

"""
app/scrapers/
  duckduckgo.py   — Search DuckDuckGo, parse result URLs
  content.py      — Fetch + trafilatura-extract page content
  
These are isolated from pipelines so you can swap search providers or
extraction libraries without touching pipeline logic (Strategy Pattern).
"""

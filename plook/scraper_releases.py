"""Scrape new book releases for followed authors.

Primary source: Babelio author pages (French book database).
Secondary source: Open Library search API.
Tertiary fallback: curated known releases from web research.

This module exists because Google Books API does not reliably index
recent French-language releases (especially 2025-2026).
"""

import logging
import re
import time
from datetime import datetime
from html import unescape
from typing import Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

MIN_RELEASE_YEAR = 2025
FETCH_TIMEOUT = 15.0
RATE_LIMIT_DELAY = 1.0  # seconds between requests to avoid being blocked

# User-Agent to avoid being blocked by Babelio
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

# ---------------------------------------------------------------------------
# Babelio author ID mapping
# Maps author names (as stored in the DB) to their Babelio author slug/ID.
# Format: "Author Name" -> "Slug/ID" where the URL is
# https://www.babelio.com/auteur/Slug/ID
# ---------------------------------------------------------------------------
BABELIO_AUTHORS: dict[str, str] = {
    "Anonyme": "",  # Bourbon Kid -- no reliable Babelio page
    "Alain Wegscheider": "Alain-Wegscheider/518771",
    "Bernard Minier": "Bernard-Minier/178498",
    "Camilla Lackberg": "Camilla-Lackberg/21067",
    "Franck Thilliez": "Franck-Thilliez/4017",
    "Fred Vargas": "Fred-Vargas/3344",
    "Freida McFadden": "Freida-McFadden/541498",
    "Graham Masterton": "Graham-Masterton/4798",
    "Sophie Henaff": "Sophie-Henaff/343029",
    "JR dos Santos": "Jose-Rodrigues-dos-Santos/64505",
    "Jean-Christophe Grange": "Jean-Christophe-Grange/3621",
    "Stephen King": "Stephen-King/3933",
    "Ken Follett": "Ken-Follett/3802",
    "Maxime Chattam": "Maxime-Chattam/3578",
    "Nicolas Lebel": "Nicolas-Lebel/140018",
    "Jacky Schwartzmann": "Jacky-Schwartzmann/375801",
    "Tom Sharpe": "Tom-Sharpe/4893",
}

# ---------------------------------------------------------------------------
# Curated known releases 2025-2026 (from web research, April 2026)
# This acts as a guaranteed fallback when scraping fails.
# ---------------------------------------------------------------------------
KNOWN_RELEASES: list[dict] = [
    # Franck Thilliez
    {
        "title": "A retardement",
        "author": "Franck Thilliez",
        "published_date": "2025-05-02",
        "cover_url": None,
        "source": "curated",
    },
    {
        "title": "L'autre moi",
        "author": "Franck Thilliez",
        "published_date": "2026-04-28",
        "cover_url": None,
        "source": "curated",
    },
    # Bernard Minier
    {
        "title": "H",
        "author": "Bernard Minier",
        "published_date": "2025-01-01",
        "cover_url": None,
        "source": "curated",
    },
    {
        "title": "Ruptures",
        "author": "Bernard Minier",
        "published_date": "2026-03-26",
        "cover_url": None,
        "source": "curated",
    },
    # Jean-Christophe Grange
    {
        "title": "Sans soleil, tome 1 : Disco inferno",
        "author": "Jean-Christophe Grange",
        "published_date": "2025-01-15",
        "cover_url": None,
        "source": "curated",
    },
    {
        "title": "Sans soleil, tome 2 : Le Roi des ombres",
        "author": "Jean-Christophe Grange",
        "published_date": "2025-01-15",
        "cover_url": None,
        "source": "curated",
    },
    {
        "title": "Je suis ne du diable",
        "author": "Jean-Christophe Grange",
        "published_date": "2025-10-02",
        "cover_url": None,
        "source": "curated",
    },
    # Maxime Chattam
    {
        "title": "8,2 secondes",
        "author": "Maxime Chattam",
        "published_date": "2025-11-05",
        "cover_url": None,
        "source": "curated",
    },
    # Stephen King
    {
        "title": "Never Flinch",
        "author": "Stephen King",
        "published_date": "2025-05-27",
        "cover_url": None,
        "source": "curated",
    },
    {
        "title": "Other Worlds Than These",
        "author": "Stephen King",
        "published_date": "2026-10-06",
        "cover_url": None,
        "source": "curated",
    },
    # Ken Follett
    {
        "title": "Circle of Days",
        "author": "Ken Follett",
        "published_date": "2025-09-23",
        "cover_url": None,
        "source": "curated",
    },
    # Freida McFadden
    {
        "title": "The Intruder",
        "author": "Freida McFadden",
        "published_date": "2025-10-07",
        "cover_url": None,
        "source": "curated",
    },
    {
        "title": "Dear Debbie",
        "author": "Freida McFadden",
        "published_date": "2026-01-27",
        "cover_url": None,
        "source": "curated",
    },
    # Fred Vargas
    {
        "title": "Une unique lueur",
        "author": "Fred Vargas",
        "published_date": "2026-04-08",
        "cover_url": None,
        "source": "curated",
    },
    # Anonyme (Bourbon Kid)
    {
        "title": "Noir comme l'Enfer",
        "author": "Anonyme",
        "published_date": "2025-11-06",
        "cover_url": None,
        "source": "curated",
    },
    # Nicolas Lebel
    {
        "title": "La Ruche",
        "author": "Nicolas Lebel",
        "published_date": "2025-03-05",
        "cover_url": None,
        "source": "curated",
    },
    # Sophie Henaff
    {
        "title": "Police people",
        "author": "Sophie Henaff",
        "published_date": "2025-01-01",
        "cover_url": None,
        "source": "curated",
    },
    # Jacky Schwartzmann
    {
        "title": "Bastion",
        "author": "Jacky Schwartzmann",
        "published_date": "2025-01-01",
        "cover_url": None,
        "source": "curated",
    },
    {
        "title": "Killing Me Softly",
        "author": "Jacky Schwartzmann",
        "published_date": "2026-01-01",
        "cover_url": None,
        "source": "curated",
    },
    # JR dos Santos
    {
        "title": "Protocole Chaos",
        "author": "JR dos Santos",
        "published_date": "2025-05-01",
        "cover_url": None,
        "source": "curated",
    },
    {
        "title": "Le sixieme sens",
        "author": "JR dos Santos",
        "published_date": "2026-04-09",
        "cover_url": None,
        "source": "curated",
    },
    # Graham Masterton
    {
        "title": "House of Flies",
        "author": "Graham Masterton",
        "published_date": "2025-10-09",
        "cover_url": None,
        "source": "curated",
    },
]


def _normalize(text: str) -> str:
    """Normalize text for comparison: lowercase, strip accents loosely."""
    import unicodedata
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.strip()


def _extract_year(date_str: str | None) -> int | None:
    """Extract year from a date string (YYYY, YYYY-MM, YYYY-MM-DD)."""
    if not date_str:
        return None
    match = re.match(r"(\d{4})", date_str)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Babelio scraper
# ---------------------------------------------------------------------------

def _scrape_babelio_author(author_name: str) -> list[dict]:
    """Scrape an author's bibliography from Babelio.

    Uses the Babelio author page and parses book titles and dates
    from the HTML using regex (no BeautifulSoup dependency).

    Returns a list of dicts with keys: title, author, published_date,
    cover_url, source.
    """
    slug = BABELIO_AUTHORS.get(author_name, "")
    if not slug:
        # Try to find the author via Babelio search
        return _search_babelio_author(author_name)

    url = f"https://www.babelio.com/auteur/{slug}"
    logger.debug("Fetching Babelio author page: %s", url)

    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            logger.warning("Babelio returned status %d for %s", resp.status_code, author_name)
            return []
        html = resp.text
    except Exception as exc:
        logger.warning("Failed to fetch Babelio page for %s: %s", author_name, exc)
        return []

    return _parse_babelio_books(html, author_name)


def _search_babelio_author(author_name: str) -> list[dict]:
    """Search for an author on Babelio and scrape their books."""
    search_url = f"https://www.babelio.com/recherche.php?Recherche={quote(author_name)}&item_type=auteurs"
    try:
        resp = httpx.get(search_url, headers=_HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            return []

        # Try to find the first author link in search results
        match = re.search(r'href="(/auteur/[^"]+)"', resp.text)
        if not match:
            return []

        author_url = f"https://www.babelio.com{match.group(1)}"
        resp2 = httpx.get(author_url, headers=_HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True)
        if resp2.status_code != 200:
            return []

        return _parse_babelio_books(resp2.text, author_name)
    except Exception as exc:
        logger.warning("Babelio search failed for %s: %s", author_name, exc)
        return []


def _parse_babelio_books(html: str, author_name: str) -> list[dict]:
    """Parse book entries from a Babelio author page HTML.

    Babelio author pages list books with titles and dates in various
    formats. We extract them using regex patterns.
    """
    results = []

    # Pattern 1: Book entries with date in format DD/MM/YYYY or MM/YYYY or YYYY
    # Babelio uses divs with class "livre_resume" or similar structures.
    # The books are typically in <a> tags linking to /livres/...
    # with dates nearby in the HTML.

    # Extract all book links with their surrounding context
    # Babelio book URLs look like: /livres/AuthorName-BookTitle/123456
    book_blocks = re.findall(
        r'href="(/livres/[^"]+)"[^>]*>([^<]+)</a>.*?'
        r'(?:(\d{2}/\d{2}/\d{4})|(\d{2}/\d{4})|(\b20[12]\d\b))',
        html,
        re.DOTALL,
    )

    for block in book_blocks:
        book_url, raw_title, date_full, date_month, date_year = block
        title = unescape(raw_title).strip()

        # Parse the date
        pub_date = None
        if date_full:
            try:
                dt = datetime.strptime(date_full, "%d/%m/%Y")
                pub_date = dt.strftime("%Y-%m-%d")
            except ValueError:
                pass
        elif date_month:
            try:
                dt = datetime.strptime(date_month, "%m/%Y")
                pub_date = dt.strftime("%Y-%m")
            except ValueError:
                pass
        elif date_year:
            pub_date = date_year

        year = _extract_year(pub_date)
        if year is not None and year >= MIN_RELEASE_YEAR:
            # Try to extract cover image from nearby HTML
            cover_url = None
            cover_match = re.search(
                rf'<img[^>]+src="(https://[^"]*babelio[^"]*couverture[^"]*)"[^>]*>.*?{re.escape(title[:20])}',
                html,
                re.DOTALL | re.IGNORECASE,
            )
            if cover_match:
                cover_url = cover_match.group(1)

            results.append({
                "title": title,
                "author": author_name,
                "published_date": pub_date,
                "cover_url": cover_url,
                "source": "babelio",
            })

    # Fallback: simpler pattern -- just look for years near book titles
    if not results:
        # Find all book titles on the page
        title_matches = re.findall(
            r'href="/livres/[^"]+">([^<]+)</a>',
            html,
        )
        # Find all years on the page
        year_matches = re.findall(r'\b(202[5-9]|203\d)\b', html)

        if title_matches and year_matches:
            # Associate titles with nearby years (rough heuristic)
            for title_raw in title_matches:
                title = unescape(title_raw).strip()
                if not title or len(title) < 2:
                    continue
                # Check if this title appears near a recent year in the HTML
                for year_str in year_matches:
                    year = int(year_str)
                    if year >= MIN_RELEASE_YEAR:
                        # Check proximity in HTML
                        title_pos = html.find(title)
                        year_pos = html.find(year_str, max(0, title_pos - 500))
                        if 0 <= title_pos and 0 <= year_pos and abs(title_pos - year_pos) < 500:
                            results.append({
                                "title": title,
                                "author": author_name,
                                "published_date": year_str,
                                "cover_url": None,
                                "source": "babelio",
                            })
                            break  # one year per title

    # Deduplicate by title
    seen_titles = set()
    unique = []
    for r in results:
        norm = _normalize(r["title"])
        if norm not in seen_titles:
            seen_titles.add(norm)
            unique.append(r)

    return unique


# ---------------------------------------------------------------------------
# Open Library fallback
# ---------------------------------------------------------------------------

def _search_openlibrary(author_name: str) -> list[dict]:
    """Search Open Library for recent books by an author.

    Open Library's search API is free and does not require an API key.
    """
    url = "https://openlibrary.org/search.json"
    params = {
        "author": author_name,
        "sort": "new",
        "limit": 10,
        "fields": "title,first_publish_year,cover_i,author_name",
    }

    try:
        resp = httpx.get(url, params=params, timeout=FETCH_TIMEOUT)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception as exc:
        logger.warning("Open Library search failed for %s: %s", author_name, exc)
        return []

    results = []
    for doc in data.get("docs", []):
        year = doc.get("first_publish_year")
        if year is not None and year >= MIN_RELEASE_YEAR:
            cover_url = None
            cover_id = doc.get("cover_i")
            if cover_id:
                cover_url = f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"

            results.append({
                "title": doc.get("title", ""),
                "author": author_name,
                "published_date": str(year),
                "cover_url": cover_url,
                "source": "openlibrary",
            })

    return results


# ---------------------------------------------------------------------------
# Curated releases lookup
# ---------------------------------------------------------------------------

def _get_curated_releases(author_name: str) -> list[dict]:
    """Return curated known releases for a given author."""
    norm_name = _normalize(author_name)
    return [
        r for r in KNOWN_RELEASES
        if _normalize(r["author"]) == norm_name
        and _extract_year(r["published_date"]) is not None
        and _extract_year(r["published_date"]) >= MIN_RELEASE_YEAR
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape_author_releases(author_name: str) -> list[dict]:
    """Find new releases for a single author.

    Tries sources in order:
    1. Babelio (best for French market)
    2. Open Library (international, free API)
    3. Curated known releases (guaranteed data from web research)

    Results are merged and deduplicated. Each result dict has:
    - title: book title
    - author: author name
    - published_date: date string (YYYY, YYYY-MM, or YYYY-MM-DD)
    - cover_url: cover image URL or None
    - source: "babelio", "openlibrary", or "curated"
    """
    all_results = []
    seen_titles: set[str] = set()

    def _add_results(results: list[dict]):
        for r in results:
            norm = _normalize(r["title"])
            if norm not in seen_titles:
                seen_titles.add(norm)
                all_results.append(r)

    # 1. Babelio
    try:
        babelio = _scrape_babelio_author(author_name)
        _add_results(babelio)
        logger.info(
            "Babelio: found %d recent book(s) for %s", len(babelio), author_name
        )
    except Exception as exc:
        logger.warning("Babelio scraping error for %s: %s", author_name, exc)

    time.sleep(RATE_LIMIT_DELAY)

    # 2. Open Library
    try:
        ol = _search_openlibrary(author_name)
        _add_results(ol)
        logger.info(
            "Open Library: found %d recent book(s) for %s", len(ol), author_name
        )
    except Exception as exc:
        logger.warning("Open Library search error for %s: %s", author_name, exc)

    time.sleep(RATE_LIMIT_DELAY)

    # 3. Curated (always included as guaranteed fallback)
    curated = _get_curated_releases(author_name)
    _add_results(curated)
    if curated:
        logger.info(
            "Curated: added %d known release(s) for %s", len(curated), author_name
        )

    return all_results


def scrape_all_authors(author_names: list[str]) -> dict[str, list[dict]]:
    """Scrape new releases for multiple authors.

    Args:
        author_names: List of author names to check.

    Returns:
        Dict mapping author name to list of release dicts.
    """
    results = {}
    for name in author_names:
        logger.info("Scraping releases for: %s", name)
        releases = scrape_author_releases(name)
        if releases:
            results[name] = releases
            logger.info(
                "Found %d total release(s) for %s", len(releases), name
            )
        else:
            logger.info("No recent releases found for %s", name)
    return results

"""Client API pour Google Books et Open Library."""

import logging
import os
import time
import urllib.parse
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

GOOGLE_BOOKS_KEY = os.getenv("GOOGLE_BOOKS_KEY", "")
GOOGLE_BASE = "https://www.googleapis.com/books/v1"
OL_BASE = "https://openlibrary.org"
OL_COVERS = "https://covers.openlibrary.org"

_last_google_call = 0.0
_last_ol_call = 0.0

TIMEOUT = 10.0


def _rate_limit_google():
    """Enforce 0.1s minimum between Google Books API calls."""
    global _last_google_call
    elapsed = time.time() - _last_google_call
    if elapsed < 0.1:
        time.sleep(0.1 - elapsed)
    _last_google_call = time.time()


def _rate_limit_ol():
    """Enforce 0.5s minimum between Open Library API calls."""
    global _last_ol_call
    elapsed = time.time() - _last_ol_call
    if elapsed < 0.5:
        time.sleep(0.5 - elapsed)
    _last_ol_call = time.time()


def _google_cover_url(volume_id: str) -> str:
    """Build a high-quality cover URL from a Google Books volume ID."""
    return (
        f"https://books.google.com/books/content"
        f"?id={volume_id}&printsec=frontcover&img=1&zoom=2"
    )


def _extract_isbn(volume_info: dict) -> Optional[str]:
    """Extract ISBN-13 (preferred) or ISBN-10 from volume info."""
    identifiers = volume_info.get("industryIdentifiers", [])
    isbn13 = None
    isbn10 = None
    for ident in identifiers:
        if ident.get("type") == "ISBN_13":
            isbn13 = ident.get("identifier")
        elif ident.get("type") == "ISBN_10":
            isbn10 = ident.get("identifier")
    return isbn13 or isbn10


def _parse_volume(item: dict) -> dict:
    """Parse a Google Books API volume item into a clean dict."""
    info = item.get("volumeInfo", {})
    volume_id = item.get("id", "")

    # Cover: prefer imageLinks with zoom=2, fallback to constructed URL
    cover_url = None
    image_links = info.get("imageLinks", {})
    if image_links:
        thumb = image_links.get("thumbnail", "")
        if thumb:
            cover_url = thumb.replace("zoom=1", "zoom=2")
    if not cover_url and volume_id:
        cover_url = _google_cover_url(volume_id)

    return {
        "google_books_id": volume_id,
        "title": info.get("title", ""),
        "authors": info.get("authors", []),
        "cover_url": cover_url,
        "synopsis": info.get("description"),
        "categories": info.get("categories", []),
        "page_count": info.get("pageCount"),
        "rating": info.get("averageRating"),
        "rating_count": info.get("ratingsCount"),
        "isbn": _extract_isbn(info),
        "publisher": info.get("publisher"),
        "published_date": info.get("publishedDate"),
    }


def search_books(query: str, max_results: int = 6) -> list[dict]:
    """Search Google Books and return a list of parsed volume dicts.

    Args:
        query: Search query string (title, author, ISBN, etc.)
        max_results: Maximum number of results to return (default 6).

    Returns:
        List of dicts with keys: google_books_id, title, authors, cover_url,
        synopsis, categories, page_count, rating, isbn, etc.
        Returns [] on error or if no API key is configured.
    """
    if not GOOGLE_BOOKS_KEY:
        log.warning("GOOGLE_BOOKS_KEY not set — skipping Google Books search")
        return []

    _rate_limit_google()
    params = {
        "q": query,
        "maxResults": max_results,
        "key": GOOGLE_BOOKS_KEY,
    }

    try:
        resp = httpx.get(
            f"{GOOGLE_BASE}/volumes", params=params, timeout=TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        return [_parse_volume(item) for item in items]
    except Exception as exc:
        log.error("Google Books search error for %r: %s", query, exc)
        return []


def get_book_details(volume_id: str) -> Optional[dict]:
    """Get full details for a single Google Books volume.

    Args:
        volume_id: The Google Books volume ID.

    Returns:
        Parsed volume dict, or None on error.
    """
    if not GOOGLE_BOOKS_KEY:
        log.warning("GOOGLE_BOOKS_KEY not set — skipping Google Books details")
        return None

    _rate_limit_google()
    params = {"key": GOOGLE_BOOKS_KEY}

    try:
        resp = httpx.get(
            f"{GOOGLE_BASE}/volumes/{volume_id}", params=params, timeout=TIMEOUT
        )
        resp.raise_for_status()
        return _parse_volume(resp.json())
    except Exception as exc:
        log.error("Google Books details error for %r: %s", volume_id, exc)
        return None


def search_author_books(
    author_name: str, order_by: str = "newest", max_results: int = 10
) -> list[dict]:
    """Search all books by a given author on Google Books.

    Args:
        author_name: Author's name.
        order_by: Sort order — "newest" or "relevance".
        max_results: Maximum results to return.

    Returns:
        List of parsed volume dicts.
    """
    if not GOOGLE_BOOKS_KEY:
        log.warning("GOOGLE_BOOKS_KEY not set — skipping author search")
        return []

    _rate_limit_google()
    params = {
        "q": f"inauthor:{author_name}",
        "orderBy": order_by,
        "maxResults": max_results,
        "key": GOOGLE_BOOKS_KEY,
    }

    try:
        resp = httpx.get(
            f"{GOOGLE_BASE}/volumes", params=params, timeout=TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        return [_parse_volume(item) for item in items]
    except Exception as exc:
        log.error("Google Books author search error for %r: %s", author_name, exc)
        return []


def ol_get_cover(isbn: str) -> Optional[str]:
    """Get a book cover URL from Open Library by ISBN.

    Args:
        isbn: ISBN-10 or ISBN-13.

    Returns:
        Cover image URL (Large size), or None if not found.
    """
    if not isbn:
        return None

    _rate_limit_ol()

    try:
        # Check if the book exists on Open Library first
        resp = httpx.get(
            f"{OL_BASE}/isbn/{isbn}.json", timeout=TIMEOUT
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        # If it exists, the cover URL is deterministic
        cover_url = f"{OL_COVERS}/b/isbn/{isbn}-L.jpg"
        return cover_url
    except Exception as exc:
        log.error("Open Library cover lookup error for ISBN %s: %s", isbn, exc)
        return None


def _verify_image_url(url: str, timeout: float = 3.0) -> bool:
    """Verify that a URL returns an actual image via HEAD request.

    Returns True if the response has an image content-type and status 200.
    """
    try:
        resp = httpx.head(url, timeout=timeout, follow_redirects=True)
        if resp.status_code != 200:
            return False
        content_type = resp.headers.get("content-type", "")
        return content_type.startswith("image/")
    except Exception:
        return False


def find_best_cover(
    title: str, author: str = "", google_books_id: str = None
) -> Optional[str]:
    """Find the best available book cover using multiple sources.

    Strategy chain:
    1. If google_books_id provided, try direct Google Books cover URL
    2. Search Google Books API by title+author, extract cover
    3. Search Open Library by title+author
    4. Try Open Library by ISBN if found
    5. Fallback: construct Google Books cover URL from search result ID

    Returns cover URL or None if absolutely nothing found.
    """
    # --- Step 1: Direct Google Books cover from provided ID ---
    if google_books_id:
        direct_url = _google_cover_url(google_books_id)
        if _verify_image_url(direct_url):
            log.info("Cover found via direct Google Books ID %s", google_books_id)
            return direct_url

    # --- Step 2: Search Google Books API ---
    search_query = title
    if author:
        search_query = f"{title} {author}"

    gb_results = search_books(search_query, max_results=3)
    found_gb_id = None
    found_isbn = None

    for result in gb_results:
        # Track first google_books_id and isbn for fallbacks
        if not found_gb_id and result.get("google_books_id"):
            found_gb_id = result["google_books_id"]
        if not found_isbn and result.get("isbn"):
            found_isbn = result["isbn"]

        cover = result.get("cover_url")
        if cover and _verify_image_url(cover):
            log.info("Cover found via Google Books search for %r", title)
            return cover

    # --- Step 3: Search Open Library by title+author ---
    _rate_limit_ol()
    try:
        ol_params = {"title": title, "limit": "1"}
        if author:
            ol_params["author"] = author
        ol_search_url = f"{OL_BASE}/search.json?{urllib.parse.urlencode(ol_params)}"
        resp = httpx.get(ol_search_url, timeout=TIMEOUT)
        resp.raise_for_status()
        ol_data = resp.json()
        docs = ol_data.get("docs", [])
        if docs:
            doc = docs[0]
            cover_id = doc.get("cover_i")
            if cover_id:
                ol_cover_url = f"{OL_COVERS}/b/id/{cover_id}-L.jpg"
                if _verify_image_url(ol_cover_url):
                    log.info("Cover found via Open Library search for %r", title)
                    return ol_cover_url
            # Extract ISBN from OL results for step 4
            ol_isbns = doc.get("isbn", [])
            if ol_isbns and not found_isbn:
                found_isbn = ol_isbns[0]
    except Exception as exc:
        log.error("Open Library search error for %r: %s", title, exc)

    # --- Step 4: Try Open Library by ISBN ---
    if found_isbn:
        ol_isbn_cover = ol_get_cover(found_isbn)
        if ol_isbn_cover and _verify_image_url(ol_isbn_cover):
            log.info("Cover found via Open Library ISBN %s for %r", found_isbn, title)
            return ol_isbn_cover

    # --- Step 5: Fallback to constructed Google Books URL ---
    if found_gb_id:
        fallback_url = _google_cover_url(found_gb_id)
        log.info("Using fallback Google Books cover URL for %r (ID: %s)", title, found_gb_id)
        return fallback_url

    log.warning("No cover found for %r by %r", title, author)
    return None

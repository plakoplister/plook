"""New release detection for followed authors.

Uses a multi-source strategy:
1. Babelio scraper + Open Library + curated releases (best for French market)
2. Google Books API (fallback, poor coverage for recent French releases)
"""

import logging
import re
import time

from sqlalchemy.orm import Session

from plook.models import Author, Book, Alert
from plook.books_api import search_author_books
from plook.scraper_releases import scrape_author_releases

logger = logging.getLogger(__name__)

MIN_RELEASE_YEAR = 2025


def _extract_year(published_date: str | None) -> int | None:
    """Extract the year from a published_date string.

    Handles formats: "YYYY", "YYYY-MM", "YYYY-MM-DD".
    Returns None if the date is missing or unparseable.
    """
    if not published_date:
        return None
    match = re.match(r"^(\d{4})", published_date)
    return int(match.group(1)) if match else None


def check_new_releases(db: Session) -> list[Alert]:
    """Check multiple sources for new releases from followed authors.

    For each followed author, searches using a multi-source strategy:
    1. Babelio scraper + Open Library + curated known releases
    2. Google Books API (fallback)

    If a book is not already in the DB (neither in books nor in alerts),
    creates a new Alert. Rate-limits requests between authors.

    Returns:
        List of newly created Alert objects.
    """
    # Purge obsolete alerts whose release year is before MIN_RELEASE_YEAR
    obsolete = (
        db.query(Alert)
        .filter(Alert.release_date.isnot(None))
        .all()
    )
    purged = 0
    for a in obsolete:
        year = _extract_year(a.release_date)
        if year is not None and year < MIN_RELEASE_YEAR:
            db.delete(a)
            purged += 1
    if purged:
        db.commit()
        logger.info("Purged %d obsolete alerts (release year < %d).", purged, MIN_RELEASE_YEAR)

    followed_authors = db.query(Author).filter(Author.is_followed == True).all()
    if not followed_authors:
        logger.info("No followed authors, skipping release check.")
        return []

    # Collect all known google_books_ids from books and existing alerts
    known_gids = set()
    for gid_tuple in db.query(Book.google_books_id).filter(Book.google_books_id.isnot(None)).all():
        known_gids.add(gid_tuple[0])
    for gid_tuple in db.query(Alert.book_google_id).filter(Alert.book_google_id.isnot(None)).all():
        known_gids.add(gid_tuple[0])

    # Also collect known alert titles to avoid duplicating scraped releases
    known_titles: set[str] = set()
    for title_tuple in db.query(Alert.book_title).filter(Alert.book_title.isnot(None)).all():
        known_titles.add(title_tuple[0].lower().strip())
    for title_tuple in db.query(Book.title).filter(Book.title.isnot(None)).all():
        known_titles.add(title_tuple[0].lower().strip())

    new_alerts = []

    for author in followed_authors:
        logger.info("Checking new releases for: %s", author.name)

        # --- Source 1: Babelio + Open Library + curated releases ---
        try:
            scraped = scrape_author_releases(author.name)
        except Exception as exc:
            logger.error("Scraper error for %s: %s", author.name, exc)
            scraped = []

        for rel in scraped:
            title = rel.get("title", "").strip()
            if not title:
                continue

            # Skip if title already known (in books or alerts)
            if title.lower() in known_titles:
                continue

            pub_year = _extract_year(rel.get("published_date"))
            if pub_year is not None and pub_year < MIN_RELEASE_YEAR:
                continue
            if pub_year is None:
                continue

            # For scraped releases, use a synthetic google_id based on source
            synthetic_gid = f"scraped:{rel.get('source', 'unknown')}:{title.lower().replace(' ', '_')[:60]}"
            if synthetic_gid in known_gids:
                continue

            alert = Alert(
                author_id=author.id,
                book_title=title,
                book_google_id=synthetic_gid,
                cover_url=rel.get("cover_url"),
                release_date=rel.get("published_date", ""),
                seen=False,
            )
            db.add(alert)
            new_alerts.append(alert)
            known_gids.add(synthetic_gid)
            known_titles.add(title.lower())
            logger.info(
                "New release alert (via %s): %s - %s",
                rel.get("source", "scraper"), author.name, title,
            )

        # --- Source 2: Google Books (fallback) ---
        try:
            results = search_author_books(author.name, order_by="newest", max_results=5)
        except Exception as exc:
            logger.error("Error searching releases for %s: %s", author.name, exc)
            time.sleep(0.5)
            continue

        for vol in results:
            gid = vol.get("google_books_id")
            if not gid or gid in known_gids:
                continue

            title = vol.get("title", "").strip()
            if title.lower() in known_titles:
                continue

            # Reject books published before MIN_RELEASE_YEAR
            pub_year = _extract_year(vol.get("published_date"))
            if pub_year is not None and pub_year < MIN_RELEASE_YEAR:
                continue
            # Also skip if no date at all (unknown release = not a confirmed new book)
            if pub_year is None:
                continue

            alert = Alert(
                author_id=author.id,
                book_title=title,
                book_google_id=gid,
                cover_url=vol.get("cover_url"),
                release_date=vol.get("published_date", ""),
                seen=False,
            )
            db.add(alert)
            new_alerts.append(alert)
            known_gids.add(gid)
            known_titles.add(title.lower())
            logger.info("New release alert (via Google Books): %s - %s", author.name, title)

        # Update last_checked timestamp
        from datetime import datetime, timezone
        author.last_checked = datetime.now(timezone.utc)

        time.sleep(0.5)

    if new_alerts:
        db.commit()
        logger.info("Created %d new alerts.", len(new_alerts))
    else:
        db.commit()  # commit last_checked updates
        logger.info("No new releases found.")

    return new_alerts

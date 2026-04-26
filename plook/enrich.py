"""Batch enrichment of books via Google Books API.

Usage:
    python -m plook.enrich
"""

import logging
import sys
import time

from plook.books_api import GOOGLE_BOOKS_KEY, ol_get_cover, search_books
from plook.database import SessionLocal
from plook.models import Book

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BATCH_SIZE = 20


def _progress(current: int, total: int, title: str):
    """Print a simple progress bar to the terminal."""
    width = 40
    filled = int(width * current / total) if total else 0
    bar = "█" * filled + "░" * (width - filled)
    pct = (current / total * 100) if total else 0
    print(f"\r  [{bar}] {current}/{total} ({pct:.0f}%)  {title[:40]:<40}", end="", flush=True)


def enrich_books():
    """Enrich all books missing a google_books_id."""
    if not GOOGLE_BOOKS_KEY:
        log.warning(
            "GOOGLE_BOOKS_KEY is not set in .env — cannot enrich books. "
            "Add the key and re-run."
        )
        sys.exit(0)

    db = SessionLocal()
    try:
        books = db.query(Book).filter(Book.google_books_id.is_(None)).all()
        total = len(books)

        if total == 0:
            log.info("All books already enriched — nothing to do.")
            return

        log.info("Enriching %d book(s) without Google Books data...", total)
        enriched = 0
        skipped = 0

        for i, book in enumerate(books, 1):
            # Build search query from title + author
            query_parts = [book.title]
            if book.author_name:
                query_parts.append(book.author_name)
            query = " ".join(query_parts)

            _progress(i, total, book.title or "???")

            results = search_books(query, max_results=3)
            if not results:
                skipped += 1
                time.sleep(0.1)
                continue

            hit = results[0]

            # Skip if this google_books_id is already used by another book
            existing = db.query(Book).filter(Book.google_books_id == hit["google_books_id"]).first()
            if existing:
                skipped += 1
                continue

            # Update book fields
            book.google_books_id = hit["google_books_id"]
            book.cover_url = hit["cover_url"]
            book.synopsis = hit["synopsis"]
            book.google_categories = hit["categories"] or None
            book.google_rating = hit["rating"]
            book.google_rating_count = hit["rating_count"]
            book.page_count = hit["page_count"]

            if hit["isbn"]:
                book.isbn = hit["isbn"]

            # Fallback cover via Open Library if Google has none
            if not book.cover_url and book.isbn:
                ol_cover = ol_get_cover(book.isbn)
                if ol_cover:
                    book.cover_url = ol_cover

            enriched += 1

            # Commit every BATCH_SIZE books
            if enriched % BATCH_SIZE == 0:
                db.commit()
                log.info("  Committed batch (%d enriched so far)", enriched)

            time.sleep(0.1)

        # Final commit for remaining books
        db.commit()
        print()  # newline after progress bar
        log.info(
            "Done: %d enriched, %d skipped (no results), %d total.",
            enriched, skipped, total,
        )

    except KeyboardInterrupt:
        print()
        log.warning("Interrupted — committing current progress...")
        db.commit()
    except Exception:
        db.rollback()
        log.exception("Enrichment failed — rolled back.")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    enrich_books()

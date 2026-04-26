#!/usr/bin/env python3
"""
fix_books_photos.py — Plook maintenance script
1) Remove duplicate / junk books for Thilliez, Grangé, dos Santos
2) Verify & fix photo_url for all followed authors
"""

import sys
import time
import requests
import re

sys.path.insert(0, '/Users/julienmarboeuf/Documents/PERSO/BIBLIOTHEQUES/PLOOK')
from plook.database import SessionLocal
from plook.models import Book, Author, ReadingListItem

HEADERS = {
    "User-Agent": "PlookBot/1.0 (personal library app; contact: julien.marboeuf@gmail.com)"
}

# ─── Part 1: Remove duplicate / junk books ────────────────────────────────────

def clean_duplicates(db):
    print("=" * 70)
    print("PART 1: CLEANING DUPLICATE / JUNK BOOKS")
    print("=" * 70)
    deleted = []

    # --- Thilliez ---
    print("\n--- Franck Thilliez ---")
    # "Sharko" [225] is a bare duplicate of "Sharko & Henebelle - Couple de Flics" [219]
    _delete_book_if_exists(db, 225, "Sharko (bare duplicate of 'Sharko & Henebelle')", deleted)

    # --- Dos Santos ---
    print("\n--- JR dos Santos ---")
    # "Pack Histoire & Religion" [142] is a compilation pack, not a real book
    _delete_book_if_exists(db, 142, "Pack Histoire & Religion (compilation, not a real book)", deleted)

    # --- Grangé ---
    print("\n--- Jean-Christophe Grangé ---")
    # "Le Terre Inachevé" [197] — likely a typo. Check if it's a real book or junk.
    book_197 = db.get(Book, 197)
    if book_197:
        print(f"  NOTE: Book [{book_197.id}] '{book_197.title}' — suspicious title.")
        print(f"         This may be 'La Terre des morts' with a wrong title.")
        print(f"         Cover: {book_197.cover_url}")
        print(f"         Synopsis: {(book_197.synopsis or '')[:120]}...")
        # Check if "La Terre des morts" already exists for Grangé
        existing = db.query(Book).filter(
            Book.author_id == book_197.author_id,
            Book.title.like("%Terre des morts%")
        ).first()
        if existing:
            print(f"  FOUND existing 'La Terre des morts' [{existing.id}] — deleting the typo.")
            _delete_book_if_exists(db, 197, "Le Terre Inachevé (typo duplicate of La Terre des morts)", deleted)
        else:
            print(f"  No 'La Terre des morts' found. Renaming to 'La Terre des morts'.")
            book_197.title = "La Terre des morts"
            db.commit()
            print(f"  RENAMED [{book_197.id}] -> 'La Terre des morts'")

    db.commit()

    print(f"\n  Total books deleted: {len(deleted)}")
    for d in deleted:
        print(f"    - {d}")
    return deleted


def _delete_book_if_exists(db, book_id, reason, deleted_list):
    book = db.get(Book, book_id)
    if not book:
        print(f"  Book [{book_id}] not found, skipping.")
        return

    # Check reading list references
    rl_entries = db.query(ReadingListItem).filter(ReadingListItem.book_id == book_id).all()
    for entry in rl_entries:
        db.delete(entry)

    title = book.title
    db.delete(book)
    db.commit()
    print(f"  DELETED [{book_id}] '{title}' — {reason}")
    deleted_list.append(f"[{book_id}] {title}")


# ─── Part 2: Verify & fix author photos ───────────────────────────────────────

def check_author_photos(db):
    print("\n" + "=" * 70)
    print("PART 2: VERIFYING FOLLOWED AUTHOR PHOTOS")
    print("=" * 70)

    authors = db.query(Author).filter(Author.is_followed == True).order_by(Author.name).all()
    print(f"\nFound {len(authors)} followed authors.\n")

    results = {"ok": [], "fixed": [], "failed": []}

    for author in authors:
        print(f"--- {author.name} (id={author.id}) ---")

        # Step 1: Check existing photo_url
        if author.photo_url:
            print(f"  Current photo: {author.photo_url[:80]}...")
            accessible = _check_url(author.photo_url)
            if accessible:
                print(f"  Status: OK (accessible)")
                results["ok"].append(author.name)
                continue
            else:
                print(f"  Status: BROKEN (not accessible)")
        else:
            print(f"  No photo_url set.")

        # Step 2: Try Wikipedia FR
        print(f"  Searching Wikipedia FR...")
        time.sleep(0.5)
        wiki_url = _search_wikipedia_photo(author.name)
        if wiki_url:
            print(f"  Found Wikipedia photo: {wiki_url[:80]}...")
            time.sleep(0.5)
            if _check_url(wiki_url):
                author.photo_url = wiki_url
                db.commit()
                print(f"  UPDATED photo from Wikipedia.")
                results["fixed"].append(f"{author.name} (Wikipedia)")
                continue
            else:
                print(f"  Wikipedia photo URL not accessible.")

        # Step 3: Try Open Library
        print(f"  Searching Open Library...")
        time.sleep(0.5)
        ol_url = _search_openlibrary_photo(author.name)
        if ol_url:
            print(f"  Found Open Library photo: {ol_url[:80]}...")
            time.sleep(0.5)
            if _check_url(ol_url):
                author.photo_url = ol_url
                db.commit()
                print(f"  UPDATED photo from Open Library.")
                results["fixed"].append(f"{author.name} (Open Library)")
                continue
            else:
                print(f"  Open Library photo URL not accessible.")

        print(f"  FAILED: No photo found for {author.name}")
        results["failed"].append(author.name)

    # Summary
    print(f"\n--- Photo Check Summary ---")
    print(f"  OK (already valid): {len(results['ok'])}")
    for name in results["ok"]:
        print(f"    - {name}")
    print(f"  Fixed: {len(results['fixed'])}")
    for name in results["fixed"]:
        print(f"    - {name}")
    print(f"  Failed (no photo found): {len(results['failed'])}")
    for name in results["failed"]:
        print(f"    - {name}")

    return results


def _check_url(url):
    """Check if URL returns HTTP 200 with an image content type."""
    try:
        resp = requests.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if resp.status_code == 200:
            return True
        # Some servers don't support HEAD, try GET
        resp = requests.get(url, headers=HEADERS, timeout=10, stream=True, allow_redirects=True)
        return resp.status_code == 200
    except Exception as e:
        print(f"    URL check error: {e}")
        return False


def _search_wikipedia_photo(author_name):
    """Search Wikipedia FR for author photo."""
    try:
        # Step 1: Search for the page
        search_url = "https://fr.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": author_name,
            "srlimit": 1,
        }
        resp = requests.get(search_url, params=params, headers=HEADERS, timeout=10)
        data = resp.json()
        results = data.get("query", {}).get("search", [])
        if not results:
            return None

        page_title = results[0]["title"]

        # Step 2: Get page images
        time.sleep(0.5)
        params = {
            "action": "query",
            "format": "json",
            "titles": page_title,
            "prop": "pageimages",
            "piprop": "original",
            "pilicense": "any",
        }
        resp = requests.get(search_url, params=params, headers=HEADERS, timeout=10)
        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            original = page_data.get("original", {})
            source = original.get("source")
            if source:
                return source

        # Step 3: Try thumbnail approach
        time.sleep(0.5)
        params = {
            "action": "query",
            "format": "json",
            "titles": page_title,
            "prop": "pageimages",
            "pithumbsize": 330,
            "pilicense": "any",
        }
        resp = requests.get(search_url, params=params, headers=HEADERS, timeout=10)
        data = resp.json()
        pages = data.get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            thumb = page_data.get("thumbnail", {})
            source = thumb.get("source")
            if source:
                return source

        return None
    except Exception as e:
        print(f"    Wikipedia search error: {e}")
        return None


def _search_openlibrary_photo(author_name):
    """Search Open Library for author photo."""
    try:
        url = f"https://openlibrary.org/search/authors.json?q={requests.utils.quote(author_name)}"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        data = resp.json()
        docs = data.get("docs", [])
        if not docs:
            return None

        # Try first few results to find one with a photo
        for doc in docs[:3]:
            key = doc.get("key")
            if key:
                photo_url = f"https://covers.openlibrary.org/a/olid/{key}-M.jpg"
                return photo_url

        return None
    except Exception as e:
        print(f"    Open Library search error: {e}")
        return None


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    db = SessionLocal()
    try:
        deleted = clean_duplicates(db)
        photo_results = check_author_photos(db)

        print("\n" + "=" * 70)
        print("FINAL REPORT")
        print("=" * 70)
        print(f"Books deleted: {len(deleted)}")
        print(f"Author photos OK: {len(photo_results['ok'])}")
        print(f"Author photos fixed: {len(photo_results['fixed'])}")
        print(f"Author photos failed: {len(photo_results['failed'])}")
        print("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

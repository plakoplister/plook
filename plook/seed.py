"""
Seed Plook database from Excel files.

Sources:
  1. bibliotheque.xlsx — columns: Auteur, Titre, Editeur, Année, Genre (315 rows)
  2. Recap Loisirs.xlsx sheet LIVRES (header row 1) — columns: Titre, Auteur, Date de publication, Genre, Best

Usage:
  python -m plook.seed
"""

import re
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

# --- Paths -----------------------------------------------------------------

BIBLIO_PATH = Path.home() / "Documents/PERSO/BIBLIOTHEQUES/LIVRES/BIBLIOTHEQUE/bibliotheque.xlsx"
RECAP_PATH = Path.home() / "Documents/PERSO/BIBLIOTHEQUES/Recap Loisirs.xlsx"

# --- Auto-follow authors ---------------------------------------------------

AUTO_FOLLOW = [
    "Thilliez", "Grangé", "Dos Santos", "Bourbon Kid", "Schwartzmann",
    "Henaff", "Vargas", "Sharpe", "Follett", "Minier", "Lebel",
    "Chattam", "McFadden", "Masterton", "King",
]

# --- Kobo recent reads -----------------------------------------------------

KOBO_BOOKS = [
    {"title": "Ruptures", "author": "Bernard Minier", "progress": 17, "format": "ebook"},
    {"title": "Sainte Emmerderesse", "author": "Audrey Alwett", "progress": 1, "format": "ebook"},
    {"title": "L'Autre Moi", "author": "Franck Thilliez", "progress": 0, "format": "ebook"},
    {"title": "Le Sixième Sens", "author": "José Rodrigues Dos Santos", "progress": 0, "format": "ebook"},
    {"title": "In Extremis", "author": "Shutterberg", "progress": 99, "format": "ebook"},
    {"title": "La Ruche", "author": "Nicolas Lebel", "progress": 100, "format": "ebook"},
    {"title": "8,2 secondes", "author": "Maxime Chattam", "progress": 100, "format": "ebook"},
    {"title": "Les Manuscrits Perdus", "author": "Steve Berry", "progress": 9, "format": "ebook"},
    {"title": "La Femme de Ménage", "author": "Freida McFadden", "progress": 100, "format": "ebook"},
    {"title": "Le Cercle des Jours", "author": "Ken Follett", "progress": 1, "format": "ebook"},
]

# --- Helpers ---------------------------------------------------------------


def normalize(text: str) -> str:
    """Lowercase, strip accents-ish, collapse whitespace."""
    if not text:
        return ""
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def fuzzy_match(a: str, b: str, threshold: float = 0.85) -> bool:
    """Return True if two strings are similar enough."""
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio() >= threshold


def author_matches_follow(author_name: str) -> bool:
    """Check if an author name matches any auto-follow keyword."""
    name_lower = normalize(author_name)
    for keyword in AUTO_FOLLOW:
        if keyword.lower() in name_lower:
            return True
    return False


def safe_int(val) -> int | None:
    """Convert to int or None."""
    try:
        v = int(float(val))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def safe_str(val) -> str | None:
    """Convert to str or None."""
    if pd.isna(val):
        return None
    s = str(val).strip()
    return s if s else None


# --- Main seed logic -------------------------------------------------------


def load_bibliotheque() -> pd.DataFrame:
    """Load bibliotheque.xlsx and normalize columns."""
    if not BIBLIO_PATH.exists():
        print(f"  [SKIP] {BIBLIO_PATH} not found")
        return pd.DataFrame()

    df = pd.read_excel(BIBLIO_PATH)
    print(f"  bibliotheque.xlsx: {len(df)} rows, columns: {list(df.columns)}")

    # Standardize column names
    col_map = {}
    for c in df.columns:
        cl = c.lower().strip()
        if cl in ("auteur", "author"):
            col_map[c] = "auteur"
        elif cl in ("titre", "title"):
            col_map[c] = "titre"
        elif cl in ("editeur", "publisher", "éditeur"):
            col_map[c] = "editeur"
        elif cl in ("année", "annee", "year"):
            col_map[c] = "annee"
        elif cl in ("genre",):
            col_map[c] = "genre"
    df = df.rename(columns=col_map)
    return df


def load_recap() -> pd.DataFrame:
    """Load Recap Loisirs.xlsx sheet LIVRES and normalize columns."""
    if not RECAP_PATH.exists():
        print(f"  [SKIP] {RECAP_PATH} not found")
        return pd.DataFrame()

    df = pd.read_excel(RECAP_PATH, sheet_name="LIVRES", header=1)
    print(f"  Recap Loisirs LIVRES: {len(df)} rows, columns: {list(df.columns)}")

    # Rename to standard
    col_map = {}
    for c in df.columns:
        cl = c.lower().strip()
        if cl == "titre":
            col_map[c] = "titre"
        elif cl == "auteur":
            col_map[c] = "auteur"
        elif cl in ("date de publication",):
            col_map[c] = "annee"
        elif cl == "genre":
            col_map[c] = "genre"
        elif cl == "best":
            col_map[c] = "best"
    df = df.rename(columns=col_map)
    return df


def merge_sources(biblio: pd.DataFrame, recap: pd.DataFrame) -> list[dict]:
    """Merge two sources by fuzzy title+author match. Returns list of book dicts."""
    books: list[dict] = []
    used_recap_idx: set[int] = set()

    # Index recap for faster lookup
    recap_records = []
    if not recap.empty:
        recap_records = recap.to_dict("records")
        recap_idx_list = list(range(len(recap_records)))
    else:
        recap_idx_list = []

    # Process bibliotheque first (richer metadata: publisher, year)
    if not biblio.empty:
        for _, row in biblio.iterrows():
            titre = safe_str(row.get("titre"))
            auteur = safe_str(row.get("auteur"))
            if not titre:
                continue

            book = {
                "title": titre,
                "author_name": auteur,
                "publisher": safe_str(row.get("editeur")),
                "year": safe_int(row.get("annee")),
                "genre": safe_str(row.get("genre")),
                "is_best": False,
                "is_read": True,  # in library = read
            }

            # Try to match with recap
            for idx in recap_idx_list:
                if idx in used_recap_idx:
                    continue
                rec = recap_records[idx]
                rec_titre = safe_str(rec.get("titre"))
                rec_auteur = safe_str(rec.get("auteur"))
                if not rec_titre:
                    continue

                title_match = fuzzy_match(titre, rec_titre)
                author_match = True
                if auteur and rec_auteur:
                    author_match = fuzzy_match(auteur, rec_auteur)

                if title_match and author_match:
                    used_recap_idx.add(idx)
                    # Enrich from recap
                    if not book["genre"] and safe_str(rec.get("genre")):
                        book["genre"] = safe_str(rec.get("genre"))
                    if not book["year"]:
                        book["year"] = safe_int(rec.get("annee"))
                    if rec.get("best") == 1 or rec.get("best") == 1.0:
                        book["is_best"] = True
                    break

            books.append(book)

    # Add unmatched recap entries
    for idx in recap_idx_list:
        if idx in used_recap_idx:
            continue
        rec = recap_records[idx]
        titre = safe_str(rec.get("titre"))
        if not titre:
            continue
        books.append({
            "title": titre,
            "author_name": safe_str(rec.get("auteur")),
            "publisher": None,
            "year": safe_int(rec.get("annee")),
            "genre": safe_str(rec.get("genre")),
            "is_best": rec.get("best") == 1 or rec.get("best") == 1.0,
            "is_read": True,
        })

    return books


def run_seed():
    """Main seed entry point."""
    from plook.database import init_db, SessionLocal
    from plook.models import Book, Author

    print("=== Plook Seed ===\n")

    # 1. Init DB
    print("[1/6] Initializing database...")
    init_db()
    db = SessionLocal()

    # 2. Load sources
    print("[2/6] Loading Excel sources...")
    biblio = load_bibliotheque()
    recap = load_recap()

    # 3. Merge
    print("[3/6] Merging sources (fuzzy match)...")
    merged = merge_sources(biblio, recap)
    print(f"  Merged: {len(merged)} unique books")

    # 4. Seed authors
    print("[4/6] Seeding authors...")
    author_names: set[str] = set()
    for b in merged:
        if b["author_name"]:
            author_names.add(b["author_name"])
    for kb in KOBO_BOOKS:
        author_names.add(kb["author"])

    authors_created = 0
    authors_updated = 0
    for name in sorted(author_names):
        existing = db.query(Author).filter(Author.name == name).first()
        should_follow = author_matches_follow(name)
        if existing:
            if should_follow and not existing.is_followed:
                existing.is_followed = True
                authors_updated += 1
        else:
            author = Author(name=name, is_followed=should_follow)
            db.add(author)
            authors_created += 1
    db.commit()
    print(f"  Authors: {authors_created} created, {authors_updated} updated, {len(author_names)} total")

    # Build author lookup
    author_lookup: dict[str, int] = {}
    for a in db.query(Author).all():
        author_lookup[a.name] = a.id

    # 5. Seed books
    print("[5/6] Seeding books...")
    books_created = 0
    books_updated = 0

    for b in merged:
        # Find by title + author (idempotent)
        q = db.query(Book).filter(Book.title == b["title"])
        if b["author_name"]:
            q = q.filter(Book.author_name == b["author_name"])
        existing = q.first()

        author_id = author_lookup.get(b["author_name"]) if b["author_name"] else None

        if existing:
            # Update fields that might be missing
            if b["publisher"] and not existing.publisher:
                existing.publisher = b["publisher"]
            if b["year"] and not existing.year:
                existing.year = b["year"]
            if b["genre"] and not existing.genre:
                existing.genre = b["genre"]
            if b["is_best"]:
                existing.is_best = True
            if author_id and not existing.author_id:
                existing.author_id = author_id
            existing.is_read = True
            books_updated += 1
        else:
            book = Book(
                title=b["title"],
                author_name=b["author_name"],
                author_id=author_id,
                publisher=b["publisher"],
                year=b["year"],
                genre=b["genre"],
                is_best=b["is_best"],
                is_read=b["is_read"],
            )
            db.add(book)
            books_created += 1

    db.commit()

    # 6. Seed Kobo books
    print("[6/6] Seeding Kobo recent reads...")
    kobo_created = 0
    kobo_updated = 0

    for kb in KOBO_BOOKS:
        q = db.query(Book).filter(Book.title == kb["title"])
        existing = q.first()
        author_id = author_lookup.get(kb["author"])
        is_finished = kb["progress"] >= 100

        if existing:
            existing.reading_progress = kb["progress"]
            existing.format = kb["format"]
            if is_finished:
                existing.is_read = True
            if author_id and not existing.author_id:
                existing.author_id = author_id
            if not existing.author_name:
                existing.author_name = kb["author"]
            kobo_updated += 1
        else:
            book = Book(
                title=kb["title"],
                author_name=kb["author"],
                author_id=author_id,
                format=kb["format"],
                reading_progress=kb["progress"],
                is_read=is_finished,
            )
            db.add(book)
            kobo_created += 1

    db.commit()

    # Stats
    total_books = db.query(Book).count()
    total_authors = db.query(Author).count()
    total_best = db.query(Book).filter(Book.is_best.is_(True)).count()
    total_read = db.query(Book).filter(Book.is_read.is_(True)).count()
    total_followed = db.query(Author).filter(Author.is_followed.is_(True)).count()

    print(f"\n=== Seed complete ===")
    print(f"  Books:   {total_books} total ({books_created} new from Excel, {books_updated} updated)")
    print(f"  Kobo:    {kobo_created} new, {kobo_updated} updated")
    print(f"  Authors: {total_authors} total, {total_followed} followed")
    print(f"  Best:    {total_best}")
    print(f"  Read:    {total_read}")

    db.close()


if __name__ == "__main__":
    run_seed()

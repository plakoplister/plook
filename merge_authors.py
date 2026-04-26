"""
Merge duplicate authors in the Plook database using fuzzy matching.
"""
import time
import unicodedata
import re
from difflib import SequenceMatcher
from collections import defaultdict

# Wait for the other agent to finish normalizing author names
print("Waiting 30 seconds for author normalization to complete...")
time.sleep(30)
print("Starting duplicate author detection.\n")

from plook.database import SessionLocal
from plook.models import Author, Book

FUZZY_THRESHOLD = 0.85


def normalize_name(name: str) -> str:
    """Normalize a name: lowercase, remove accents, strip extra spaces."""
    # Remove accents
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Lowercase and strip
    ascii_name = ascii_name.lower().strip()
    # Collapse multiple spaces
    ascii_name = re.sub(r"\s+", " ", ascii_name)
    return ascii_name


def canonical_tokens(name: str) -> str:
    """Sort tokens alphabetically so 'KING STEPHEN' matches 'Stephen King'."""
    return " ".join(sorted(normalize_name(name).split()))


def are_duplicates(name_a: str, name_b: str) -> bool:
    """Check if two author names are duplicates using fuzzy matching."""
    norm_a = normalize_name(name_a)
    norm_b = normalize_name(name_b)

    # Direct normalized match
    if norm_a == norm_b:
        return True

    # Token-sorted match (handles "KING STEPHEN" vs "Stephen King")
    if canonical_tokens(name_a) == canonical_tokens(name_b):
        return True

    # Fuzzy match on normalized names
    ratio = SequenceMatcher(None, norm_a, norm_b).ratio()
    if ratio >= FUZZY_THRESHOLD:
        return True

    # Fuzzy match on token-sorted names
    ratio_sorted = SequenceMatcher(None, canonical_tokens(name_a), canonical_tokens(name_b)).ratio()
    if ratio_sorted >= FUZZY_THRESHOLD:
        return True

    # Check if one name is a short form of another (initials)
    # e.g. "JR Dos Santos" vs "Jose Rodrigues Dos Santos"
    tokens_a = normalize_name(name_a).split()
    tokens_b = normalize_name(name_b).split()
    if _initials_match(tokens_a, tokens_b) or _initials_match(tokens_b, tokens_a):
        return True

    return False


def _initials_match(short_tokens: list, long_tokens: list) -> bool:
    """Check if short_tokens could be initials/abbreviation of long_tokens.
    E.g. ['jr', 'dos', 'santos'] matches ['jose', 'rodrigues', 'dos', 'santos']
    """
    if len(short_tokens) >= len(long_tokens):
        return False

    # Check if the last N tokens match (surname part)
    # and the first tokens are initials of the long form
    # Find common suffix (surname)
    common_suffix = 0
    for i in range(1, min(len(short_tokens), len(long_tokens)) + 1):
        if short_tokens[-i] == long_tokens[-i]:
            common_suffix = i
        else:
            break

    if common_suffix == 0:
        return False

    # Check remaining short tokens are initials of remaining long tokens
    short_prefix = short_tokens[:len(short_tokens) - common_suffix]
    long_prefix = long_tokens[:len(long_tokens) - common_suffix]

    if not short_prefix or not long_prefix:
        return False

    # Join the short prefix into one string and check if it's initials
    short_joined = "".join(short_prefix).replace(".", "").replace("-", "")
    long_initials = "".join(t[0] for t in long_prefix)

    return short_joined == long_initials


def author_richness(author: Author, book_count: int) -> tuple:
    """Score an author by data richness: (book_count, has_bio, has_photo, id)."""
    has_bio = 1 if author.bio else 0
    has_photo = 1 if author.photo_url else 0
    return (book_count, has_bio, has_photo, -author.id)  # prefer lower id as tiebreaker


def find_duplicate_groups(authors: list) -> list:
    """Find groups of duplicate authors using fuzzy matching."""
    visited = set()
    groups = []

    for i, a in enumerate(authors):
        if a.id in visited:
            continue
        group = [a]
        visited.add(a.id)

        for j in range(i + 1, len(authors)):
            b = authors[j]
            if b.id in visited:
                continue
            if are_duplicates(a.name, b.name):
                group.append(b)
                visited.add(b.id)

        if len(group) > 1:
            groups.append(group)

    return groups


def merge_authors():
    db = SessionLocal()
    try:
        authors = db.query(Author).all()
        print(f"Total authors in database: {len(authors)}")

        # Count books per author
        book_counts = defaultdict(int)
        for author in authors:
            book_counts[author.id] = len(author.books)

        # Find duplicate groups
        groups = find_duplicate_groups(authors)
        print(f"Duplicate groups found: {len(groups)}\n")

        total_merged = 0
        total_deleted = 0

        for group in groups:
            # Sort by richness to pick the best one
            group.sort(key=lambda a: author_richness(a, book_counts[a.id]), reverse=True)
            primary = group[0]
            duplicates = group[1:]

            names = [f'"{a.name}" ({len(a.books)} books)' for a in group]
            print(f"=== Merge group ===")
            print(f"  Keeping: \"{primary.name}\" (id={primary.id}, {len(primary.books)} books)")

            for dup in duplicates:
                books_to_transfer = db.query(Book).filter(Book.author_id == dup.id).all()
                print(f"  Merging: \"{dup.name}\" (id={dup.id}, {len(books_to_transfer)} books) -> \"{primary.name}\"")

                # Transfer books
                for book in books_to_transfer:
                    book.author_id = primary.id
                    book.author_name = primary.name
                    total_merged += 1

                # Delete the duplicate author
                db.delete(dup)
                total_deleted += 1

            print()

        db.commit()
        print(f"--- Summary ---")
        print(f"Duplicate groups processed: {len(groups)}")
        print(f"Books transferred: {total_merged}")
        print(f"Duplicate authors deleted: {total_deleted}")

        # Final count
        remaining = db.query(Author).count()
        print(f"Authors remaining: {remaining}")

    finally:
        db.close()


if __name__ == "__main__":
    merge_authors()

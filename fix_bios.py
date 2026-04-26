"""
Fix author bios: fetch French biographies from Wikipedia FR for followed authors
and authors with 3+ books.
"""
import time
import re
import sys
import urllib.parse
import requests
from sqlalchemy import func

# --- OBLIGATOIRE : attendre 60s pour laisser les autres agents finir ---
print("Attente de 60 secondes (autres agents en cours sur la DB)...")
time.sleep(60)
print("C'est parti !\n")

from plook.database import SessionLocal, init_db
from plook.models import Author, Book

init_db()
db = SessionLocal()

# 1. Charger les auteurs cibles
followed = db.query(Author).filter(Author.is_followed == True).all()
followed_ids = {a.id for a in followed}

# Authors with 3+ books
prolific = (
    db.query(Author.id)
    .join(Book, Book.author_id == Author.id)
    .group_by(Author.id)
    .having(func.count(Book.id) >= 3)
    .all()
)
prolific_ids = {row[0] for row in prolific}

target_ids = followed_ids | prolific_ids
authors = db.query(Author).filter(Author.id.in_(target_ids)).all()

print(f"Auteurs cibles : {len(authors)}")
for a in authors:
    tag = []
    if a.id in followed_ids:
        tag.append("followed")
    if a.id in prolific_ids:
        tag.append("3+ livres")
    print(f"  - {a.name} [{', '.join(tag)}] bio={'OUI' if a.bio else 'NON'} photo={'OUI' if a.photo_url else 'NON'}")
print()


def is_english_bio(text):
    """Heuristic: if the bio has common English words and few French ones, it's English."""
    if not text:
        return False
    lower = text.lower()
    en_markers = [" is a ", " was a ", " born ", " known for ", " he is ", " she is ", " their "]
    fr_markers = [" est un ", " est une ", " née ", " né ", " connu ", " connue ", " français", " auteur "]
    en_score = sum(1 for m in en_markers if m in lower)
    fr_score = sum(1 for m in fr_markers if m in lower)
    return en_score > fr_score and en_score >= 1


def wiki_fr_search(name):
    """Try Wikipedia FR API with the author name. Returns (extract, thumbnail_url) or (None, None)."""
    # Build variants to try
    variants = [name]

    # Replace spaces with underscores
    wiki_name = name.replace(" ", "_")
    variants_wiki = [wiki_name]

    # Try common accent corrections for known cases
    accent_map = {
        "Grange": "Grangé",
        "Echenoz": "Echenoz",
        "Lemaitre": "Lemaître",
        "Musso": "Musso",
    }
    for plain, accented in accent_map.items():
        if plain in name and accented != plain:
            variants_wiki.append(name.replace(plain, accented).replace(" ", "_"))

    # Also try adding " (écrivain)" suffix for disambiguation
    for v in list(variants_wiki):
        variants_wiki.append(v + "_(écrivain)")
        variants_wiki.append(v + "_(romancier)")
        variants_wiki.append(v + "_(auteur)")

    headers = {
        "User-Agent": "PlookBot/1.0 (personal book library; contact: plook@example.com)"
    }

    for variant in variants_wiki:
        encoded = urllib.parse.quote(variant, safe="")
        url = f"https://fr.wikipedia.org/api/rest_v1/page/summary/{encoded}"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                extract = data.get("extract", "")
                thumbnail = data.get("thumbnail", {}).get("source")
                if extract and len(extract) > 50:
                    return extract, thumbnail
        except Exception as e:
            print(f"    [WARN] Wikipedia error for {variant}: {e}")
        time.sleep(0.3)

    return None, None


def open_library_search(name):
    """Fallback: try Open Library for author bio."""
    headers = {
        "User-Agent": "PlookBot/1.0 (personal book library)"
    }
    try:
        search_url = f"https://openlibrary.org/search/authors.json?q={urllib.parse.quote(name)}"
        resp = requests.get(search_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            docs = data.get("docs", [])
            if docs:
                key = docs[0].get("key")
                if key:
                    author_url = f"https://openlibrary.org/authors/{key}.json"
                    resp2 = requests.get(author_url, headers=headers, timeout=10)
                    if resp2.status_code == 200:
                        author_data = resp2.json()
                        bio = author_data.get("bio")
                        if isinstance(bio, dict):
                            bio = bio.get("value", "")
                        photo_id = author_data.get("photos", [None])[0] if author_data.get("photos") else None
                        photo_url = f"https://covers.openlibrary.org/a/id/{photo_id}-M.jpg" if photo_id else None
                        if bio and len(bio) > 30:
                            return bio, photo_url
    except Exception as e:
        print(f"    [WARN] Open Library error for {name}: {e}")
    return None, None


# 2. Process each author
updated = 0
skipped = 0
failed = []

for author in authors:
    needs_bio = not author.bio or is_english_bio(author.bio)
    needs_photo = not author.photo_url

    if not needs_bio and not needs_photo:
        print(f"[SKIP] {author.name} — bio FR et photo deja OK")
        skipped += 1
        continue

    reason = []
    if not author.bio:
        reason.append("pas de bio")
    elif is_english_bio(author.bio):
        reason.append("bio en anglais")
    if needs_photo:
        reason.append("pas de photo")

    print(f"[FETCH] {author.name} ({', '.join(reason)})")

    # Try Wikipedia FR first
    extract, thumb = wiki_fr_search(author.name)

    if extract:
        if needs_bio:
            author.bio = extract
            print(f"  -> Bio Wikipedia FR : {extract[:80]}...")
        if needs_photo and thumb:
            author.photo_url = thumb
            print(f"  -> Photo Wikipedia : {thumb[:60]}...")
        updated += 1
    else:
        # Fallback: Open Library
        print(f"  -> Wikipedia FR: rien trouve, essai Open Library...")
        time.sleep(0.5)
        ol_bio, ol_photo = open_library_search(author.name)
        if ol_bio:
            if needs_bio:
                author.bio = ol_bio
                print(f"  -> Bio Open Library : {ol_bio[:80]}...")
            if needs_photo and ol_photo:
                author.photo_url = ol_photo
                print(f"  -> Photo Open Library : {ol_photo[:60]}...")
            updated += 1
        else:
            print(f"  -> AUCUNE bio trouvee")
            failed.append(author.name)

    time.sleep(0.5)  # Rate limit

# 3. Commit
db.commit()
db.close()

print(f"\n{'='*50}")
print(f"RESULTATS:")
print(f"  Auteurs traites : {len(authors)}")
print(f"  Deja OK (skip)  : {skipped}")
print(f"  Mis a jour      : {updated}")
print(f"  Echecs          : {len(failed)}")
if failed:
    print(f"  Noms en echec   : {', '.join(failed)}")
print(f"{'='*50}")

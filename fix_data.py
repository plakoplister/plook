"""
Script de correction de donnees Plook.
1. Marquer tous les livres comme lus sauf 3
2. Ajouter 7 livres a la PAL (reading_list)
3. Normaliser les noms d'auteurs en "Prenom Nom"
4. Enrichir les bios d'auteurs suivis via Open Library
"""

import re
import time
import sys
import os

# Ajouter le dossier parent au path pour importer plook
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

import httpx
from datetime import datetime
from plook.database import SessionLocal, init_db
from plook.models import Book, Author, ReadingListItem


def fix_is_read(db):
    """1. Marquer tous les livres comme lus sauf 3 titres specifiques."""
    print("\n=== 1. Marquage is_read ===")

    unread_titles = [
        "L'Autre Moi",
        "Le Cercle des Jours",
        "Le Sixième Sens",
    ]

    # Tout marquer comme lu
    updated = db.query(Book).filter(Book.is_read == False).update({Book.is_read: True})
    print(f"  Livres passes a is_read=True : {updated}")

    # Remettre les 3 a non-lu
    for title in unread_titles:
        book = db.query(Book).filter(Book.title == title).first()
        if book:
            book.is_read = False
            print(f"  Remis a is_read=False : {book.title} ({book.author_name})")
        else:
            print(f"  ATTENTION: livre non trouve : {title}")

    db.commit()
    print("  OK")


def fix_reading_list(db):
    """2. Ajouter 7 livres a la PAL."""
    print("\n=== 2. Ajout PAL (reading_list) ===")

    pal_books = [
        ("Regression", "Fabrice Papillon"),
        ("L'Enterrement de Serge", "Stephane Carlier"),
        ("Je suis ne du diable", "Jean-Christophe Grange"),
        ("Le secret des secrets", "Dan Brown"),
        ("L'Autre Moi", "Franck Thilliez"),
        ("Le Cercle des Jours", "Ken Follett"),
        ("Le Sixième Sens", "José Rodrigues Dos Santos"),
    ]

    # Trouver la position max actuelle
    max_pos = db.query(ReadingListItem.position).order_by(ReadingListItem.position.desc()).first()
    next_pos = (max_pos[0] + 1) if max_pos else 1

    for title, author_name in pal_books:
        # Chercher le livre par titre (approx)
        book = db.query(Book).filter(Book.title == title).first()
        if not book:
            # Recherche plus souple
            book = db.query(Book).filter(Book.title.ilike(f"%{title}%")).first()

        if not book:
            # Creer le livre
            book = Book(
                title=title,
                author_name=author_name,
                is_read=False,
                added_at=datetime.utcnow(),
            )
            # Essayer de trouver l'auteur existant
            author = db.query(Author).filter(
                (Author.name == author_name) |
                (Author.name.ilike(f"%{author_name.split()[-1]}%{author_name.split()[0]}%"))
            ).first()
            if author:
                book.author_id = author.id
            db.add(book)
            db.flush()  # pour avoir book.id
            print(f"  Livre cree : {title} par {author_name} (id={book.id})")
        else:
            print(f"  Livre existant : {book.title} (id={book.id})")

        # Verifier s'il est deja dans la PAL
        existing = db.query(ReadingListItem).filter(ReadingListItem.book_id == book.id).first()
        if existing:
            print(f"    -> Deja dans la PAL (position {existing.position})")
            continue

        entry = ReadingListItem(
            book_id=book.id,
            position=next_pos,
            priority="normal",
            added_at=datetime.utcnow(),
        )
        db.add(entry)
        print(f"    -> Ajoute a la PAL (position {next_pos})")
        next_pos += 1

    db.commit()
    print("  OK")


def normalize_author_name(name: str) -> str:
    """Convertir un nom d'auteur au format 'Prenom Nom'.

    Gere:
    - "NOM PRENOM" (tout majuscules) -> "Prenom Nom"
    - "NOM, PRENOM" -> "Prenom Nom"
    - Cas speciaux: JR Dos Santos, Anonyme, P.G. Wodehouse, noms composes
    """
    if not name:
        return name

    original = name.strip()

    # Cas speciaux a ne pas toucher
    skip_patterns = [
        "Anonyme",
        "San-Antonio",
        "SAN-ANTONIO",
        "Cummings",
        "Shutterberg",
    ]
    for pat in skip_patterns:
        if original == pat:
            if original.isupper():
                return original.title()
            return original

    # Noms composes avec &
    if " & " in original:
        # ex: "Gérard Davet & Fabrice Lhomme" ou "DAVET GÉRARD" (handled individually)
        # On ne touche pas si c'est deja au bon format
        if not original.isupper():
            return original
        # Pas de format simple pour ca, on laisse
        return original

    # Format "NOM, PRENOM" -> "PRENOM NOM"
    if "," in original:
        parts = [p.strip() for p in original.split(",", 1)]
        if len(parts) == 2:
            last_name, first_name = parts
            return _title_case_name(first_name) + " " + _title_case_name(last_name)

    # Format tout MAJUSCULES: "NOM PRENOM" ou "NOM PRENOM PRENOM"
    # On detecte si le nom est en majuscules (au moins 2 mots)
    words = original.split()
    if len(words) >= 2 and _is_all_caps_name(original):
        return _convert_caps_name(original)

    # Deja en format mixte (Prenom Nom) -> ne rien faire
    return original


def _is_all_caps_name(name: str) -> bool:
    """Verifie si un nom est en format MAJUSCULES (NOM PRENOM)."""
    # Ignorer les particules, initiales, parentheses
    words = name.split()
    caps_count = 0
    for w in words:
        # Ignorer les mots entre parentheses
        if w.startswith("("):
            continue
        # Ignorer les particules courtes
        clean = w.strip("()")
        if clean.lower() in ("de", "du", "des", "la", "le", "l'", "d'", "von", "van"):
            continue
        if clean == clean.upper() and len(clean) > 1 and clean.isalpha():
            caps_count += 1
        elif "." in clean:  # initiales comme M.C., P.G.
            caps_count += 1
        elif clean == clean.upper() and "-" in w:  # NOM-COMPOSE
            caps_count += 1

    return caps_count >= 2


def _convert_caps_name(name: str) -> str:
    """Convertir 'NOM PRENOM' majuscules en 'Prenom Nom'.

    Le premier mot est generalement le NOM de famille.
    Cas speciaux: particules, suffixes entre parentheses.
    """
    # Retirer le contenu entre parentheses a la fin
    paren_match = re.search(r'\s*\(.*\)\s*$', name)
    paren_suffix = ""
    clean_name = name
    if paren_match:
        paren_suffix = paren_match.group().strip()
        clean_name = name[:paren_match.start()].strip()

    words = clean_name.split()

    # Cas speciaux connus
    special_mappings = {
        "DOS SANTOS JOSÉ RODRIGUES": "José Rodrigues Dos Santos",
        "RODRIGUES DOS SANTOS JOSE": "Jose Rodrigues Dos Santos",
        "SANTOS JOSE RODRIGUES DOS": "Jose Rodrigues Dos Santos",
        "MAALOUF DE L'ACADÉMIE FRANÇAISE AMIN": "Amin Maalouf",
        "RAVENNE GIACOMETTI": "Giacometti Ravenne",
        "PATRICK SÉBASTIEN": "Patrick Sébastien",
        "OLDE HEUVELT THOMAS": "Thomas Olde Heuvelt",
        "BEATON M. C.": "M. C. Beaton",
        "ELLORY R.J.": "R.J. Ellory",
        "WODEHOUSE P.G.": "P.G. Wodehouse",
        "VAN REYBROUCK DAVID": "David Van Reybrouck",
    }
    if clean_name in special_mappings:
        result = special_mappings[clean_name]
        if paren_suffix:
            # On ne garde pas le suffixe pour simplifier
            pass
        return result

    if len(words) < 2:
        return _title_case_name(name)

    # Detection des particules (de, du, van, von, etc.)
    # Strategie: le premier "groupe" est le nom de famille, le reste est le prenom
    # Sauf si on detecte une particule au milieu

    # Approche simple: le premier mot est le NOM, le reste PRENOM
    # Sauf particules connues apres le premier mot
    last_name_parts = [words[0]]
    first_name_parts = []
    i = 1

    # Verifier si les mots suivants sont des particules (font partie du nom)
    # En fait, en format CAPS francais, c'est generalement NOM PRENOM
    # Le NOM peut etre compose (ex: OLDE HEUVELT -> nom de famille)
    # Mais c'est rare et gere en cas special ci-dessus.

    first_name_parts = words[1:]

    if not first_name_parts:
        return _title_case_name(clean_name)

    first = " ".join(_title_case_name(w) for w in first_name_parts)
    last = _title_case_name(last_name_parts[0])

    return f"{first} {last}"


def _title_case_name(word: str) -> str:
    """Met en Title Case un mot, gerant les accents, tirets, apostrophes."""
    if not word:
        return word

    # Garder les initiales comme elles sont (M.C., P.G., R.J.)
    if "." in word and len(word) <= 5:
        return word.upper() if word.isupper() else word

    # Noms a tirets
    if "-" in word:
        return "-".join(_title_case_name(part) for part in word.split("-"))

    # Particules
    lower_word = word.lower()
    if lower_word in ("de", "du", "des", "la", "le", "l'", "d'", "von", "van", "dos"):
        return lower_word.capitalize()  # Capitaliser quand c'est un mot seul

    # Standard title case
    return word.capitalize()


def fix_author_names(db):
    """3. Normaliser les noms d'auteurs en 'Prenom Nom'."""
    print("\n=== 3. Normalisation des noms d'auteurs ===")

    # Traiter la table authors
    authors = db.query(Author).all()
    author_renames = {}  # old_name -> new_name pour mettre a jour les books aussi

    for author in authors:
        new_name = normalize_author_name(author.name)
        if new_name != author.name:
            # Verifier qu'un auteur avec ce nom n'existe pas deja
            existing = db.query(Author).filter(Author.name == new_name, Author.id != author.id).first()
            if existing:
                print(f"  SKIP (doublon): {author.name} -> {new_name} (id={existing.id} existe deja)")
                author_renames[author.name] = (new_name, existing.id, author.id)
                continue
            old = author.name
            author.name = new_name
            print(f"  Author: {old} -> {new_name}")

    db.flush()

    # Traiter author_name dans la table books
    books = db.query(Book).filter(Book.author_name.isnot(None)).all()
    for book in books:
        new_name = normalize_author_name(book.author_name)
        if new_name != book.author_name:
            old = book.author_name
            book.author_name = new_name
            # pas besoin de print pour chaque livre, trop verbeux

    db.commit()

    # Compter les livres mis a jour
    print(f"  Livres: author_name normalise pour les livres concernes")
    print("  OK")


def enrich_author_bios(db):
    """4. Enrichir les bios d'auteurs suivis via Open Library."""
    print("\n=== 4. Enrichissement bios auteurs suivis ===")

    followed = db.query(Author).filter(Author.is_followed == True).all()
    print(f"  {len(followed)} auteurs suivis trouves")

    client = httpx.Client(timeout=15)

    for author in followed:
        # Normaliser le nom pour la recherche (enlever accents n'est pas necessaire,
        # Open Library gere bien)
        search_name = author.name
        print(f"\n  --- {search_name} ---")

        # Si deja une bio, skip
        if author.bio and len(author.bio) > 50:
            print(f"    Bio deja presente ({len(author.bio)} chars), skip")
            continue

        try:
            # Etape 1: chercher l'auteur
            search_url = f"https://openlibrary.org/search/authors.json?q={search_name.replace(' ', '+')}"
            resp = client.get(search_url)
            resp.raise_for_status()
            data = resp.json()

            if not data.get("docs"):
                print(f"    Aucun resultat Open Library")
                continue

            # Prendre le premier resultat
            author_doc = data["docs"][0]
            ol_key = author_doc.get("key")
            print(f"    Trouve: {author_doc.get('name')} (key={ol_key})")

            # Etape 2: recuperer les details
            detail_url = f"https://openlibrary.org/authors/{ol_key}.json"
            resp2 = client.get(detail_url)
            resp2.raise_for_status()
            detail = resp2.json()

            # Bio
            bio = detail.get("bio")
            if isinstance(bio, dict):
                bio = bio.get("value", "")
            if bio:
                author.bio = bio[:2000]  # Limiter la taille
                print(f"    Bio trouvee ({len(bio)} chars)")
            else:
                print(f"    Pas de bio disponible")

            # Photo
            photos = detail.get("photos")
            if photos:
                photo_id = photos[0]
                if photo_id and photo_id > 0:
                    author.photo_url = f"https://covers.openlibrary.org/a/id/{photo_id}-M.jpg"
                    print(f"    Photo: {author.photo_url}")
            elif not author.photo_url:
                # Essayer via l'ID de l'auteur
                author.photo_url = f"https://covers.openlibrary.org/a/olid/{ol_key}-M.jpg"
                print(f"    Photo (olid): {author.photo_url}")

            time.sleep(0.5)  # Rate limiting

        except Exception as e:
            print(f"    Erreur: {e}")
            continue

    db.commit()
    client.close()
    print("\n  OK")


def main():
    print("=" * 60)
    print("PLOOK - Script de correction de donnees")
    print("=" * 60)

    init_db()
    db = SessionLocal()

    try:
        fix_is_read(db)
        fix_reading_list(db)
        fix_author_names(db)
        enrich_author_bios(db)

        print("\n" + "=" * 60)
        print("Toutes les corrections ont ete appliquees.")
        print("=" * 60)
    except Exception as e:
        db.rollback()
        print(f"\nERREUR: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()

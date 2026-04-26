"""
Script de correction des noms d'auteurs dans Plook.
1. Normalise tous les noms au format "Prenom Nom"
2. Fusionne les doublons (ALL CAPS -> version correcte)
3. Met a jour author_name dans books
"""

import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from plook.database import SessionLocal, init_db
from plook.models import Author, Book


# --- Particules a mettre en minuscule (sauf en debut de nom) ---
PARTICULES = {"de", "du", "des", "la", "le", "l'", "d'", "von", "van", "dos"}

# --- Cas speciaux: mapping exact -> nom correct ---
# Cles en MAJUSCULES pour matching insensible a la casse
SPECIAL_CASES = {
    "DOS SANTOS JOSÉ RODRIGUES": "Jose Rodrigues dos Santos",
    "DOS SANTOS JOSE RODRIGUES": "Jose Rodrigues dos Santos",
    "RODRIGUES DOS SANTOS JOSE": "Jose Rodrigues dos Santos",
    "SANTOS JOSE RODRIGUES DOS": "Jose Rodrigues dos Santos",
    "SANTOS JOSÉ RODRIGUES DOS": "Jose Rodrigues dos Santos",
    "JOSE RODRIGUES DOS SANTOS": "Jose Rodrigues dos Santos",
    "JOSÉ RODRIGUES DOS SANTOS": "José Rodrigues dos Santos",
    "MAALOUF DE L'ACADÉMIE FRANÇAISE AMIN": "Amin Maalouf",
    "RAVENNE GIACOMETTI": "Giacometti Ravenne",
    "PATRICK SÉBASTIEN": "Patrick Sébastien",
    "OLDE HEUVELT THOMAS": "Thomas Olde Heuvelt",
    "BEATON M. C.": "M.C. Beaton",
    "BEATON M.C.": "M.C. Beaton",
    "M. C. BEATON": "M.C. Beaton",
    "ELLORY R.J.": "R.J. Ellory",
    "WODEHOUSE P.G.": "P.G. Wodehouse",
    "VAN REYBROUCK DAVID": "David van Reybrouck",
    "SAN-ANTONIO": "San-Antonio",
    "EMPOLI GIULIANO DA": "Giuliano da Empoli",
    "PROFESSEUR DIDIER RAOULT": "Didier Raoult",
}

# Mapping pour les noms deja en casse mixte qui sont mal formates
MIXED_CASE_FIXES = {
    "M. C. Beaton": "M.C. Beaton",
    "C. Beaton M.": "M.C. Beaton",
    "Beaton M. C.": "M.C. Beaton",
    "Empoli Giuliano Da": "Giuliano da Empoli",
    "JR Dos Santos": "JR dos Santos",
    "Jose Rodrigues Dos Santos": "Jose Rodrigues dos Santos",
    "José Rodrigues Dos Santos": "José Rodrigues dos Santos",
    "David Van Reybrouck": "David van Reybrouck",
    "Freida Mcfadden": "Freida McFadden",
    "Professeur Didier Raoult": "Didier Raoult",
    "Sandriné Destombes": "Sandrine Destombes",
    "Patrick Sebastien": "Patrick Sébastien",
}

# --- Noms a ne pas toucher du tout ---
SKIP_NAMES = {
    "Anonyme", "Anonyme (bourbon kid)", "Cummings", "Shutterberg",
    "San-Antonio", "N/A", "Inconnu", "",
    "Giacometti Ravenne",   # duo d'auteurs
    "Christina Lauren",      # duo d'auteurs
    "M.C. Beaton",
    "P.G. Wodehouse",
    "R.J. Ellory",
}


def titlecase_word(word: str, is_first: bool = False) -> str:
    """Met un mot en Title Case, gerant tirets, apostrophes, initiales."""
    if not word:
        return word

    # Initiales: M.C., P.G., R.J.
    if re.match(r'^[A-Z]\.[A-Z]\.?$', word):
        return word
    if word.upper() == "JR":
        return "JR"

    # Noms a tirets: JEAN-CHRISTOPHE -> Jean-Christophe
    if "-" in word:
        parts = word.split("-")
        return "-".join(titlecase_word(p, is_first=(is_first and i == 0))
                        for i, p in enumerate(parts))

    lower = word.lower()

    # Particules: en minuscule sauf si c'est le premier mot
    if lower in PARTICULES:
        return lower.capitalize() if is_first else lower

    # Standard: premiere lettre majuscule, reste minuscule
    return word.capitalize()


def is_all_caps(name: str) -> bool:
    """Detecte si un nom est en MAJUSCULES (au moins 2 mots significatifs en caps)."""
    words = name.split()
    caps_words = 0
    for w in words:
        clean = w.strip("()")
        if clean.lower() in PARTICULES:
            continue
        if len(clean) <= 1:
            continue
        alpha_only = re.sub(r'[^a-zA-ZÀ-ÿ]', '', clean)
        if alpha_only and alpha_only == alpha_only.upper() and len(alpha_only) > 1:
            caps_words += 1
        elif "." in clean:
            caps_words += 1
    return caps_words >= 2


def normalize_author_name(name: str) -> str:
    """Normalise un nom d'auteur au format 'Prenom Nom'."""
    if not name or name.strip() in SKIP_NAMES:
        return name

    original = name.strip()

    # 1. Verifier les corrections manuelles en casse mixte
    if original in MIXED_CASE_FIXES:
        return MIXED_CASE_FIXES[original]

    # 2. Verifier les cas speciaux (match insensible a la casse)
    upper_key = original.upper().strip()
    for key, value in SPECIAL_CASES.items():
        if upper_key == key.upper():
            return value

    # 3. Format "Nom, Prenom" -> "Prenom Nom"
    if "," in original:
        parts = [p.strip() for p in original.split(",", 1)]
        if len(parts) == 2 and parts[1]:
            last_name, first_name = parts
            first = " ".join(titlecase_word(w, is_first=(i == 0))
                             for i, w in enumerate(first_name.split()))
            last = " ".join(titlecase_word(w) for w in last_name.split())
            return f"{first} {last}"

    # 4. Format TOUT MAJUSCULES: "NOM PRENOM" -> "Prenom Nom"
    if is_all_caps(original):
        return _convert_caps_name(original)

    # 5. Deja en format mixte -> verifier particules seulement
    words = original.split()
    if len(words) >= 1:
        result_words = []
        for i, w in enumerate(words):
            if w.lower() in PARTICULES and i > 0:
                result_words.append(w.lower())
            else:
                result_words.append(w)
        return " ".join(result_words)

    return original


def _convert_caps_name(name: str) -> str:
    """Convertit 'NOM PRENOM' (tout majuscules) en 'Prenom Nom'."""
    # Retirer contenu entre parentheses a la fin
    paren_match = re.search(r'\s*\(.*\)\s*$', name)
    clean_name = name
    if paren_match:
        clean_name = name[:paren_match.start()].strip()

    words = clean_name.split()
    if len(words) < 2:
        return titlecase_word(clean_name, is_first=True)

    # Le premier mot est le NOM de famille, le reste le PRENOM
    last_name = words[0]
    first_name_words = words[1:]

    first_parts = []
    for i, w in enumerate(first_name_words):
        first_parts.append(titlecase_word(w, is_first=(i == 0)))

    last_part = titlecase_word(last_name, is_first=False)

    return f"{' '.join(first_parts)} {last_part}"


def merge_duplicate_authors(db):
    """Fusionne les auteurs doublons: reassigne les livres, supprime le doublon."""
    print("\n--- Fusion des auteurs doublons ---")
    authors = db.query(Author).all()
    merged_count = 0

    # Grouper par nom normalise
    name_to_authors = {}
    for author in authors:
        normalized = normalize_author_name(author.name)
        if normalized not in name_to_authors:
            name_to_authors[normalized] = []
        name_to_authors[normalized].append(author)

    for normalized_name, author_list in name_to_authors.items():
        if len(author_list) <= 1:
            continue

        # Choisir l'auteur "principal" (celui dont le nom est deja correct, ou le plus ancien)
        # Preferer celui qui a deja des livres, une bio, ou le nom correct
        def score(a):
            s = 0
            if a.name == normalized_name:
                s += 100
            if not a.name.isupper():
                s += 50
            if a.bio:
                s += 30
            if a.photo_url:
                s += 20
            if a.is_followed:
                s += 10
            s += len(a.books) * 5
            return s

        author_list.sort(key=score, reverse=True)
        keep = author_list[0]
        duplicates = author_list[1:]

        for dup in duplicates:
            # Reassigner les livres du doublon vers l'auteur principal
            books_reassigned = 0
            for book in dup.books:
                book.author_id = keep.id
                books_reassigned += 1

            # Copier les infos manquantes
            if not keep.bio and dup.bio:
                keep.bio = dup.bio
            if not keep.photo_url and dup.photo_url:
                keep.photo_url = dup.photo_url
            if dup.is_followed and not keep.is_followed:
                keep.is_followed = True

            print(f"  MERGE: {dup.name} (id={dup.id}) -> {keep.name} (id={keep.id}) [{books_reassigned} livres reassignes]")
            db.delete(dup)
            merged_count += 1

        # S'assurer que l'auteur principal a le bon nom
        if keep.name != normalized_name:
            old = keep.name
            keep.name = normalized_name
            print(f"  RENAME: {old} -> {normalized_name} (id={keep.id})")

    db.flush()
    print(f"\n  Total fusions: {merged_count}")
    return merged_count


def fix_remaining_authors(db):
    """Corrige les noms des auteurs non-doublons."""
    print("\n--- Corrections noms auteurs restants ---")
    authors = db.query(Author).all()
    changes = []

    for author in authors:
        new_name = normalize_author_name(author.name)
        if new_name and new_name != author.name:
            old = author.name
            author.name = new_name
            changes.append((author.id, old, new_name))
            print(f"  id={author.id:>3} | {old:<45} -> {new_name}")

    if not changes:
        print("  Aucune correction necessaire.")

    db.flush()
    print(f"\n  Total: {len(changes)} corriges")
    return changes


def fix_books(db):
    """Met a jour author_name dans books pour correspondre a l'auteur lie."""
    print("\n--- Corrections table BOOKS (author_name) ---")
    books = db.query(Book).filter(Book.author_name.isnot(None)).all()
    book_changes = []

    for book in books:
        # Si le livre a un author_id, utiliser le nom de l'auteur lie
        if book.author_id and book.author:
            if book.author_name != book.author.name:
                old = book.author_name
                book.author_name = book.author.name
                book_changes.append((book.id, old, book.author_name))
                continue

        # Sinon, normaliser independamment
        new_name = normalize_author_name(book.author_name)
        if new_name and new_name != book.author_name:
            old = book.author_name
            book.author_name = new_name
            book_changes.append((book.id, old, book.author_name))

    if not book_changes:
        print("  Aucune correction necessaire.")
    else:
        for bid, old, new in book_changes:
            print(f"  book_id={bid:>3} | {old:<45} -> {new}")

    db.flush()
    print(f"\n  Total livres corriges: {len(book_changes)}")
    return book_changes


def main():
    print("=" * 65)
    print("PLOOK - Correction des noms d'auteurs")
    print("=" * 65)

    init_db()
    db = SessionLocal()

    try:
        # Etape 1: Fusionner les doublons
        merge_duplicate_authors(db)

        # Etape 2: Corriger les noms restants
        fix_remaining_authors(db)

        # Etape 3: Corriger author_name dans books
        fix_books(db)

        # Commit final
        db.commit()

        # Verification finale
        print("\n" + "=" * 65)
        print("VERIFICATION FINALE")
        print("=" * 65)
        authors = db.query(Author).order_by(Author.name).all()
        print(f"\nTotal auteurs: {len(authors)}")
        # Chercher les noms qui pourraient encore etre problematiques
        problems = []
        for a in authors:
            if a.name.isupper() and a.name not in SKIP_NAMES:
                problems.append(f"  CAPS: {a.name} (id={a.id})")
            elif "," in a.name:
                problems.append(f"  VIRGULE: {a.name} (id={a.id})")
        if problems:
            print("\nNoms potentiellement problematiques:")
            for p in problems:
                print(p)
        else:
            print("Aucun nom problematique detecte.")

        print("\n" + "=" * 65)
        print("Corrections appliquees avec succes.")
        print("=" * 65)

    except Exception as e:
        db.rollback()
        print(f"\nERREUR: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()

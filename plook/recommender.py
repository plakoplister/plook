"""Claude AI-powered book recommendation engine for Plook."""

import json
import logging
import os

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from plook.models import Book, Author, Dislike, ReadingListItem
from plook.cache import get_cached, set_cached

load_dotenv()

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"


def _build_reader_profile(db: Session) -> str:
    """Build a complete reader profile from the database and known preferences."""

    from collections import Counter
    from sqlalchemy import func

    # --- Stats generales ---
    total_read = db.query(func.count(Book.id)).filter(Book.is_read == True).scalar() or 0

    # --- 20 derniers livres lus ---
    recent_books = (
        db.query(Book)
        .filter(Book.is_read == True)
        .order_by(Book.read_at.desc().nullslast(), Book.added_at.desc())
        .limit(20)
        .all()
    )
    recent_list = []
    for b in recent_books:
        score_str = f" (note: {b.score}/5)" if b.score else ""
        best_str = " [COUP DE COEUR]" if b.is_best else ""
        recent_list.append(f"- {b.title} de {b.author_name or 'Inconnu'}{score_str}{best_str}")

    # --- Coups de coeur ---
    best_books = (
        db.query(Book)
        .filter(Book.is_best == True)
        .order_by(Book.read_at.desc().nullslast())
        .all()
    )
    best_list = [f"- {b.title} de {b.author_name or 'Inconnu'}" for b in best_books]

    # --- Livres mal notes (score <= 2) ---
    low_scored = (
        db.query(Book)
        .filter(Book.score <= 2, Book.score.isnot(None))
        .all()
    )
    low_list = [f"- {b.title} de {b.author_name or 'Inconnu'} ({b.score}/5)" for b in low_scored]

    # --- Recos refusees (dislikes) ---
    dislikes = db.query(Dislike).all()
    dislike_list = [f"- {d.title}" for d in dislikes]

    # --- Genres les plus lus (depuis google_categories) ---
    genre_counter = Counter()
    books_with_cats = db.query(Book.google_categories).filter(
        Book.google_categories.isnot(None),
        Book.is_read == True,
    ).all()
    for (cats,) in books_with_cats:
        if isinstance(cats, list):
            for cat in cats:
                genre_counter[cat] += 1
        elif isinstance(cats, str):
            genre_counter[cats] += 1
    top_genres = genre_counter.most_common(15)
    genre_list = [f"- {genre}: {count} livres" for genre, count in top_genres]

    # --- PAL (Pile a Lire) ---
    pal_items = (
        db.query(ReadingListItem)
        .join(Book, ReadingListItem.book_id == Book.id)
        .order_by(ReadingListItem.position.asc().nullslast())
        .all()
    )
    pal_list = [f"- {item.book.title}" for item in pal_items if item.book]
    pal_titles = [item.book.title for item in pal_items if item.book]

    # --- Auteurs avec nombre de livres ---
    author_stats = (
        db.query(Author.name, func.count(Book.id))
        .join(Book, Book.author_id == Author.id)
        .group_by(Author.name)
        .order_by(func.count(Book.id).desc())
        .limit(30)
        .all()
    )
    author_list = [f"- {name}: {count} livres" for name, count in author_stats]

    # --- Tous les titres possedes (pour exclusion) ---
    all_titles = [b.title for b in db.query(Book.title).all()]
    exclude_titles = sorted(set(all_titles + pal_titles))

    # Load the validated reader profile from file
    profile_path = os.path.join(os.path.dirname(__file__), "profil_lecteur.md")
    try:
        with open(profile_path, "r", encoding="utf-8") as f:
            reader_profile_md = f.read()
    except FileNotFoundError:
        reader_profile_md = "(profil lecteur non trouve)"
        logger.warning("profil_lecteur.md not found at %s", profile_path)

    profile = f"""PROFIL LECTEUR COMPLET:

{reader_profile_md}

== Stats ==
{total_read} livres lus au total.

== Coups de coeur ==
{chr(10).join(best_list) if best_list else "(aucun)"}

== Livres mal notes (a eviter ce style) ==
{chr(10).join(low_list) if low_list else "(aucun)"}

== Recommandations refusees (ne JAMAIS re-proposer) ==
{chr(10).join(dislike_list) if dislike_list else "(aucune)"}

== Genres les plus lus ==
{chr(10).join(genre_list) if genre_list else "(pas de donnees de genres)"}

== 20 derniers livres lus ==
{chr(10).join(recent_list) if recent_list else "(aucun enregistre comme lu)"}

== Auteurs en bibliotheque (nombre de livres) ==
{chr(10).join(author_list) if author_list else "(aucun auteur)"}

== Pile a Lire (PAL) — NE PAS recommander non plus ==
{chr(10).join(pal_list) if pal_list else "(vide)"}

== Livres deja possedes ou en PAL (NE PAS recommander ceux-ci) ==
{', '.join(exclude_titles[:150]) if exclude_titles else "(aucun)"}
"""
    return profile


def _build_prompt(profile: str) -> str:
    """Build the Claude prompt for recommendations."""
    return f"""{profile}

---

En te basant sur ce profil complet, recommande exactement 7 livres que ce lecteur va adorer.

Regles:
- PAS de livres deja dans sa bibliotheque ni dans sa PAL (listes ci-dessus)
- PAS de livres deja refuses (liste "Recommandations refusees")
- PAS de romance, fantasy, ou livres au rythme lent
- Evite les styles proches des livres qu'il a mal notes
- Mix: au moins 3 auteurs qu'il ne connait probablement pas + 2 valeurs sures
- Pour chaque livre, explique en 1 phrase PERCUTANTE pourquoi ca va lui plaire
- Le score est ta confiance que ca va lui plaire (1-5)

REGLES DE DIVERSITE OBLIGATOIRES (7 livres, 7 genres differents) :
- Livres 1-3 : trois thrillers/polars page-turners (varier les sous-genres : serial killer, conspiration, domestique, juridique, etc.)
- Livre 4 : un roman historique ou une saga (type Ken Follett, Umberto Eco, Arturo Perez-Reverte)
- Livre 5 : un essai ou non-fiction passionnant (histoire, vulgarisation scientifique, true crime, geopolitique)
- Livre 6 : un roman d'horreur ou de SF sombre (atmosphere oppressante, pas de SF "sense of wonder")
- Livre 7 : une surprise totale (humour noir, roman noir atmosphere, auteur completement inconnu en France, ovni litteraire)
- INTERDICTION de proposer 2 livres du meme sous-genre dans les thrillers (varier : scandinave, francais, americain, asiatique)
- INTERDICTION de ne proposer que des auteurs francophones

Reponds UNIQUEMENT avec un JSON valide, sans markdown, sans commentaire:
[
  {{"title": "...", "author": "...", "why": "...", "score": 5}},
  {{"title": "...", "author": "...", "why": "...", "score": 5}},
  {{"title": "...", "author": "...", "why": "...", "score": 4}},
  {{"title": "...", "author": "...", "why": "...", "score": 4}},
  {{"title": "...", "author": "...", "why": "...", "score": 4}},
  {{"title": "...", "author": "...", "why": "...", "score": 3}},
  {{"title": "...", "author": "...", "why": "...", "score": 3}}
]"""


def _enrich_with_covers(recommendations: list[dict]) -> list[dict]:
    """Try to fetch Google Books covers for each recommendation, with Open Library fallback."""
    try:
        from plook.books_api import search_books, ol_get_cover
    except Exception:
        return recommendations

    for reco in recommendations:
        if reco.get("cover_url"):
            continue
        query = f"{reco['title']} {reco['author']}"
        try:
            results = search_books(query, max_results=1)
            if results:
                reco["cover_url"] = results[0].get("cover_url")
                if not reco.get("google_books_id"):
                    reco["google_books_id"] = results[0].get("google_books_id")
                # Fallback Open Library si pas de cover Google Books
                if not reco.get("cover_url"):
                    isbn = results[0].get("isbn")
                    if isbn:
                        ol_cover = ol_get_cover(isbn)
                        if ol_cover:
                            reco["cover_url"] = ol_cover
        except Exception as exc:
            logger.debug("Cover enrichment failed for %s: %s", reco["title"], exc)

        # Dernier fallback : construire l'URL directement si on a un google_books_id
        if not reco.get("cover_url") and reco.get("google_books_id"):
            reco["cover_url"] = (
                f"https://books.google.com/books/content"
                f"?id={reco['google_books_id']}"
                f"&printsec=frontcover&img=1&zoom=2"
            )

    return recommendations


def get_claude_recommendations(db: Session, limit: int = 7) -> list[dict]:
    """Get personalized book recommendations via Claude AI.

    Builds a full reader profile from the database and known preferences,
    sends it to Claude, parses the JSON response, and enriches with
    Google Books covers.

    Falls back gracefully if no API key is set or if Claude is unavailable.

    Args:
        db: SQLAlchemy session.
        limit: Max number of recommendations.

    Returns:
        List of dicts: {title, author, why, score, cover_url}
    """
    cached = get_cached("claude_recos")
    if cached is not None:
        return cached[:limit]

    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set -- skipping Claude recommendations.")
        return _fallback_recommendations(db, limit)

    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package not installed -- skipping Claude recommendations.")
        return _fallback_recommendations(db, limit)

    profile = _build_reader_profile(db)
    prompt = _build_prompt(profile)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            messages=[
                {"role": "user", "content": prompt}
            ],
        )

        raw_text = message.content[0].text.strip()

        # Try to extract JSON from the response
        # Handle potential markdown code blocks
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            raw_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        recommendations = json.loads(raw_text)

        if not isinstance(recommendations, list):
            logger.error("Claude returned non-list response: %s", type(recommendations))
            return _fallback_recommendations(db, limit)

        # Normalize and validate
        clean_recos = []
        for reco in recommendations[:limit]:
            clean_recos.append({
                "title": reco.get("title", ""),
                "author": reco.get("author", "Inconnu"),
                "why": reco.get("why", ""),
                "score": reco.get("score", 3),
                "cover_url": None,
            })

        # Enrich with covers from Google Books
        clean_recos = _enrich_with_covers(clean_recos)

        set_cached("claude_recos", clean_recos)
        logger.info("Claude recommendations generated: %d items.", len(clean_recos))
        return clean_recos[:limit]

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Claude response as JSON: %s", exc)
        return _fallback_recommendations(db, limit)
    except Exception as exc:
        logger.error("Claude API error: %s", exc)
        return _fallback_recommendations(db, limit)


def _fallback_recommendations(db: Session, limit: int = 7) -> list[dict]:
    """Fallback: recommend unread books from the library."""
    import random
    from datetime import date

    seed = date.today().toordinal()
    rng = random.Random(seed)

    unread = (
        db.query(Book)
        .filter(Book.is_read == False, Book.cover_url.isnot(None))
        .all()
    )
    rng.shuffle(unread)

    result = []
    for b in unread[:limit]:
        result.append({
            "title": b.title,
            "author": b.author_name or "Inconnu",
            "cover_url": b.cover_url,
            "why": "Dans ta PAL, a decouvrir",
            "score": 3,
        })
    return result


# Keep backward compatibility with the old function name
def get_recommendations(db: Session, limit: int = 7) -> list[dict]:
    """Alias for get_claude_recommendations (backward compat)."""
    return get_claude_recommendations(db, limit=limit)

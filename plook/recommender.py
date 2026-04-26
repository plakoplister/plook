"""Book recommendation engine for Plook."""

import random
from datetime import date

from sqlalchemy.orm import Session

from plook.models import Book, Author
from plook.cache import get_cached, set_cached


def _day_seed():
    today = date.today()
    return today.year * 10000 + today.month * 100 + today.day


def get_recommendations(db: Session, limit: int = 4) -> list[dict]:
    """Get book recommendations based on loved genres and authors."""
    cached = get_cached("recommendations")
    if cached is not None:
        return cached[:limit]

    rng = random.Random(_day_seed())

    # Get loved genres from best books
    best_books = db.query(Book).filter(Book.is_best == True).all()
    genre_counts = {}
    for b in best_books:
        if b.genre:
            for g in b.genre.split(","):
                g = g.strip().lower()
                if g:
                    genre_counts[g] = genre_counts.get(g, 0) + 1

    # Get followed author names
    followed = db.query(Author).filter(Author.is_followed == True).all()
    followed_names = {a.name.lower() for a in followed}

    # Try Google Books search for similar books
    try:
        from plook.books_api import search_books
    except Exception:
        search_books = None

    recommendations = []
    seen_titles = {b.title.lower() for b in db.query(Book).all()}

    # Strategy 1: Search by top genres
    top_genres = sorted(genre_counts.keys(), key=lambda g: genre_counts[g], reverse=True)[:3]
    genre_queries = {
        "thriller": "meilleur thriller francais",
        "polar": "polar francais page-turner",
        "policier": "roman policier twist",
        "science-fiction": "thriller scientifique vulgarisation",
        "humour": "roman humour noir francais",
        "histoire": "roman historique saga",
        "horreur": "thriller horreur psychologique",
    }

    if search_books:
        for genre in top_genres:
            query = genre_queries.get(genre, f"roman {genre} francais")
            try:
                results = search_books(query, max_results=4)
                for r in results:
                    if r["title"].lower() not in seen_titles:
                        recommendations.append({
                            "title": r["title"],
                            "author": ", ".join(r.get("authors", [])) if r.get("authors") else "Inconnu",
                            "cover_url": r.get("cover_url"),
                            "why": f"Genre {genre} que tu adores",
                        })
                        seen_titles.add(r["title"].lower())
                        if len(recommendations) >= limit * 2:
                            break
            except Exception:
                continue

    # Fallback: recommend unread books from library
    if len(recommendations) < limit:
        unread = db.query(Book).filter(
            Book.is_read == False,
            Book.cover_url.isnot(None)
        ).all()
        rng.shuffle(unread)
        for b in unread[:limit]:
            if b.title.lower() not in seen_titles:
                recommendations.append({
                    "title": b.title,
                    "author": b.author_name or "Inconnu",
                    "cover_url": b.cover_url,
                    "why": "Dans ta PAL, a decouvrir",
                })
                seen_titles.add(b.title.lower())

    rng.shuffle(recommendations)
    result = recommendations[:limit]

    set_cached("recommendations", result)
    return result

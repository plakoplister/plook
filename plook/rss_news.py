"""RSS news feed aggregator for Plook — fetches book news matching reader tastes.

Personalised for: thrillers, polars, romans noirs, horreur, true crime,
histoire, vulgarisation scientifique, SF sombre.
"""

import logging
import re
from datetime import datetime, timezone

import feedparser
import httpx

from plook.cache import get_cached, set_cached

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sources RSS verifiees (toutes testees OK au 2026-04-26)
# ---------------------------------------------------------------------------
RSS_FEEDS = [
    # Tier 1 - Sources specialisees polar/thriller (prioritaires)
    {"source": "K-Libre", "url": "https://www.k-libre.fr/rss/", "tags": ["polar", "noir", "thriller"]},
    {"source": "BiblioSurf", "url": "https://www.bibliosurf.com/spip.php?page=backend", "tags": ["polar", "noir", "litterature"]},

    # Tier 2 - Generalistes livres FR
    {"source": "Le Monde Livres", "url": "https://www.lemonde.fr/livres/rss_full.xml", "tags": ["general", "presse"]},
    {"source": "Le Point Livres", "url": "https://www.lepoint.fr/arc/outboundfeeds/rss/category/livres/", "tags": ["general", "presse"]},
    {"source": "Livres Hebdo", "url": "https://www.livreshebdo.fr/rss.xml", "tags": ["general", "industrie"]},
    {"source": "Eclaireur Fnac Culture", "url": "https://leclaireur.fnac.com/categorie/culture/feed/", "tags": ["general", "culture"]},

    # Tier 3 - Nouveautes / catalogues editeurs
    {"source": "Seuil Nouveautes", "url": "https://www.seuil.com/rss/nouveaute", "tags": ["nouveautes", "editeur"]},
    {"source": "Seuil A Paraitre", "url": "https://www.seuil.com/rss/a-paraitre", "tags": ["a-paraitre", "editeur"]},
    {"source": "Amalivre", "url": "https://ebook.amalivre.fr/rss/fr/rss_ebook_nouveaute.xml", "tags": ["nouveautes", "ebook"]},

    # Tier 4 - Sources anglophones
    {"source": "BookRiot", "url": "https://bookriot.com/feed", "tags": ["general", "en"]},
    {"source": "NewInBooks", "url": "https://newinbooks.com/feed/", "tags": ["nouveautes", "en"]},
]

FETCH_TIMEOUT = 15.0

# ---------------------------------------------------------------------------
# Mots-cles de genre pour le filtrage thematique
# ---------------------------------------------------------------------------
GENRE_KEYWORDS: set[str] = {
    # Polar / thriller / noir
    "thriller", "polar", "suspense", "serial killer", "enquete", "enquête",
    "meurtre", "crime", "noir", "policier", "detective", "détective",
    "mystere", "mystère", "assassinat", "tueur",
    # Horreur
    "horreur", "horrifique", "epouvante", "épouvante", "terreur",
    "cauchemar", "zombie", "gore",
    # True crime / cold case
    "true crime", "cold case", "disparition", "affaire criminelle",
    "fait divers", "faits divers", "non elucide", "non élucidé",
    # SF sombre
    "science-fiction", "dystopie", "post-apocalyptique", "cyberpunk",
    "anticipation",
    # Histoire / vulgarisation
    "histoire", "historique", "vulgarisation", "scientifique",
    # Actualite editoriale
    "sortie", "parution", "nouveaute", "nouveauté", "a paraitre",
    "à paraître", "rentree litteraire", "rentrée littéraire",
    "prix", "goncourt", "renaudot",
}

# Headers pour eviter les blocages
_HEADERS = {"User-Agent": "Plook/1.0 feedparser"}


def _parse_date(entry) -> str:
    """Extract a readable date from a feed entry."""
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                dt = datetime(*parsed[:6])
                return dt.strftime("%d/%m/%Y")
            except Exception:
                pass
    return ""


def _clean_summary(raw: str, max_len: int = 200) -> str:
    """Strip HTML tags and truncate summary."""
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "..."
    return text


def _normalize_title(title: str) -> str:
    """Normalize title for deduplication (lowercase, strip punctuation/whitespace)."""
    t = title.lower().strip()
    t = re.sub(r"[^a-zà-ÿ0-9 ]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def _matches_authors(text_lower: str, author_names_lower: set[str]) -> bool:
    """Check if *text_lower* mentions any followed author (full name or last name)."""
    for name in author_names_lower:
        if name in text_lower:
            return True
    return False


def _matches_genre(text_lower: str) -> bool:
    """Check if *text_lower* contains any genre keyword."""
    for kw in GENRE_KEYWORDS:
        if kw in text_lower:
            return True
    return False


def _entry_matches(entry, author_names_lower: set[str]) -> bool:
    """Return True if the entry is relevant to the reader's tastes.

    Matches on author name OR genre keyword in title/summary.
    """
    title = (entry.get("title", "") or "").lower()
    summary = (entry.get("summary", "") or "").lower()
    text = title + " " + summary

    if author_names_lower and _matches_authors(text, author_names_lower):
        return True
    if _matches_genre(text):
        return True
    return False


def fetch_news(followed_author_names: list[str] | None = None, limit: int = 6) -> list[dict]:
    """Fetch RSS news matching reader tastes. Cached daily.

    Articles are kept only if they match a followed author OR a genre keyword.
    No generic fallback -- if nothing matches, an empty list is returned.

    Sources are fetched in tier order (specialisees first) so the final list
    prioritises polar/thriller/noir content.

    Args:
        followed_author_names: List of author names to filter by.
        limit: Maximum number of news items to return.

    Returns:
        List of dicts: {source, title, link, date, summary}
    """
    cached = get_cached("rss_news")
    if cached is not None:
        return cached[:limit]

    author_names_lower: set[str] = set()
    if followed_author_names:
        for name in followed_author_names:
            author_names_lower.add(name.lower())
            # Also add last name only for broader matching
            parts = name.strip().split()
            if len(parts) > 1:
                author_names_lower.add(parts[-1].lower())

    matched_entries: list[dict] = []
    seen_titles: set[str] = set()

    for feed_cfg in RSS_FEEDS:
        source_name = feed_cfg["source"]
        feed_url = feed_cfg["url"]

        try:
            resp = httpx.get(
                feed_url,
                timeout=FETCH_TIMEOUT,
                follow_redirects=True,
                headers=_HEADERS,
            )
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)
        except Exception as exc:
            logger.warning("Failed to fetch RSS feed %s: %s", source_name, exc)
            continue

        source_count = 0
        for entry in feed.entries[:20]:
            if source_count >= 2:
                break  # Max 2 articles par source pour diversifier
            if not _entry_matches(entry, author_names_lower):
                continue

            title_raw = entry.get("title", "").strip()
            norm = _normalize_title(title_raw)
            if norm in seen_titles:
                continue
            seen_titles.add(norm)

            matched_entries.append({
                "source": source_name,
                "title": title_raw,
                "link": entry.get("link", ""),
                "date": _parse_date(entry),
                "summary": _clean_summary(entry.get("summary", "")),
            })
            source_count += 1

    # No fallback: only genre/author-relevant articles
    # Order is preserved from RSS_FEEDS (tier 1 first)
    result = matched_entries[:limit]

    set_cached("rss_news", result)
    return result

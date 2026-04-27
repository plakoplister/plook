"""Refresh all Plook caches: RSS news, new release alerts, Claude recommendations.

Usage:
    python -m plook.refresh_cache
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    from plook.database import init_db, SessionLocal
    init_db()
    db = SessionLocal()

    try:
        # 1. RSS News
        logger.info("--- Fetching RSS news ---")
        from plook.rss_news import fetch_news
        from plook.models import Author

        followed = db.query(Author).filter(Author.is_followed == True).all()
        author_names = [a.name for a in followed]
        news = fetch_news(author_names, limit=6)
        logger.info("RSS news: %d items fetched.", len(news))

        # 2. New release alerts (Babelio + Open Library + curated + Google Books)
        logger.info("--- Checking new releases (multi-source: Babelio, Open Library, curated, Google Books) ---")
        from plook.alerts import check_new_releases
        new_alerts = check_new_releases(db)
        logger.info("New release alerts: %d created.", len(new_alerts))
        for alert in new_alerts:
            logger.info("  -> %s: %s (%s)", alert.author.name if alert.author else "?", alert.book_title, alert.release_date)

        # 3. Claude AI recommendations
        logger.info("--- Generating Claude recommendations ---")
        from plook.recommender import get_claude_recommendations
        recos = get_claude_recommendations(db, limit=4)
        logger.info("Claude recommendations: %d items.", len(recos))

        logger.info("--- Cache refresh complete ---")

    except Exception as exc:
        logger.error("Cache refresh failed: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()

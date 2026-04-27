"""Flask app for Plook (books)."""

from datetime import date
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, jsonify

from plook.database import init_db, SessionLocal
from plook.models import Book, Author, ReadingListItem, Alert, Dislike
from plook.recommender import get_claude_recommendations
from plook.rss_news import fetch_news

BASE_DIR = Path(__file__).parent / "plook"

plook_app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
    static_url_path="/plook/static",
)

init_db()


def get_db():
    return SessionLocal()


# ==================== Pages ====================

@plook_app.route("/")
def home():
    db = get_db()
    try:
        reading_now = (
            db.query(Book)
            .filter(Book.reading_progress > 0, Book.is_read == False)
            .order_by(Book.reading_progress.desc())
            .all()
        )

        # Auteurs suivis
        followed_authors = db.query(Author).filter(Author.is_followed == True).all()
        followed_names = [a.name for a in followed_authors]

        # RSS News
        news = fetch_news(followed_names, limit=6)

        # Alerts (non vues, uniquement 2026)
        alerts = (
            db.query(Alert)
            .filter(Alert.seen == False)
            .filter(Alert.release_date.ilike("%2026%"))
            .order_by(Alert.created_at.desc())
            .limit(6)
            .all()
        )

        total_books = db.query(Book).filter(Book.is_read == True).count()
        followed_count = len(followed_authors)
        pal_count = db.query(ReadingListItem).count()

        recommendations = get_claude_recommendations(db, limit=7)

        return render_template(
            "home.html",
            request=request,
            reading_now=reading_now,
            news=news,
            alerts=alerts,
            recommendations=recommendations,
            total_books=total_books,
            followed_count=followed_count,
            pal_count=pal_count,
        )
    finally:
        db.close()


@plook_app.route("/bibliotheque")
def bibliotheque():
    db = get_db()
    try:
        books = db.query(Book).order_by(Book.author_name, Book.title).all()
        return render_template("bibliotheque.html", request=request, books=books)
    finally:
        db.close()


@plook_app.route("/livre/<int:book_id>")
def livre_detail(book_id):
    db = get_db()
    try:
        book = db.query(Book).get(book_id)
        if not book:
            return redirect("/")
        return render_template("livre.html", request=request, book=book, book_id=book_id)
    finally:
        db.close()


@plook_app.route("/auteurs")
def auteurs():
    db = get_db()
    try:
        authors = db.query(Author).order_by(Author.name).all()
        followed = [a for a in authors if a.is_followed]
        others = [a for a in authors if not a.is_followed]
        return render_template("auteurs.html", request=request, followed=followed, others=others)
    finally:
        db.close()


@plook_app.route("/pal")
def pal():
    db = get_db()
    try:
        items = (
            db.query(ReadingListItem)
            .join(Book)
            .filter(Book.is_read == False)
            .order_by(ReadingListItem.position, ReadingListItem.added_at.desc())
            .all()
        )
        return render_template("pal.html", request=request, items=items)
    finally:
        db.close()


@plook_app.route("/auteur/<int:author_id>")
def auteur_detail(author_id):
    db = get_db()
    try:
        author = db.query(Author).get(author_id)
        if not author:
            return redirect("/")
        books = db.query(Book).filter(Book.author_id == author_id).order_by(Book.year.desc()).all()
        return render_template("auteur.html", request=request, author=author, books=books)
    finally:
        db.close()


@plook_app.route("/health")
def health():
    return {"status": "ok", "app": "plook"}


# ==================== HTMX endpoints ====================

@plook_app.route("/livre/<int:book_id>/rate", methods=["POST"])
def rate_book(book_id):
    db = get_db()
    try:
        score = int(request.form.get("score", 0))
        book = db.query(Book).get(book_id)
        if book and 1 <= score <= 5:
            book.score = score
            db.commit()
        return render_template("partials/rating.html", request=request, book=book)
    finally:
        db.close()


@plook_app.route("/livre/<int:book_id>/toggle-lu", methods=["POST"])
def toggle_lu(book_id):
    db = get_db()
    try:
        book = db.query(Book).get(book_id)
        if book:
            book.is_read = not book.is_read
            db.commit()
        return render_template("partials/lu_toggle.html", request=request, book=book)
    finally:
        db.close()


@plook_app.route("/livre/<int:book_id>/progress", methods=["POST"])
def update_progress(book_id):
    db = get_db()
    try:
        progress = int(request.form.get("progress", 0))
        book = db.query(Book).get(book_id)
        if book:
            book.reading_progress = max(0, min(100, progress))
            db.commit()
        return render_template("partials/progress.html", request=request, book=book)
    finally:
        db.close()


@plook_app.route("/pal/add", methods=["POST"])
def pal_add():
    db = get_db()
    try:
        book_id = int(request.form.get("book_id", 0))
        existing = db.query(ReadingListItem).filter(ReadingListItem.book_id == book_id).first()
        if not existing:
            db.add(ReadingListItem(book_id=book_id))
            db.commit()
        return '<div class="tag">Ajoute a la PAL</div>'
    finally:
        db.close()


@plook_app.route("/auteur/<int:author_id>/toggle-follow", methods=["POST"])
def toggle_follow(author_id):
    db = get_db()
    try:
        author = db.query(Author).get(author_id)
        if author:
            author.is_followed = not author.is_followed
            db.commit()
        label = "suivi" if author.is_followed else "suivre cet auteur"
        css = "btn btn-follow active" if author.is_followed else "btn btn-follow"
        return (
            f'<button type="submit" id="follow-btn" class="{css}"'
            f' hx-post="/auteur/{author_id}/toggle-follow"'
            f' hx-target="#follow-btn" hx-swap="outerHTML">'
            f'{label}</button>'
        )
    finally:
        db.close()


# ==================== Reco actions (HTMX) ====================

@plook_app.route("/reco/add-pal", methods=["POST"])
def reco_add_pal():
    db = get_db()
    try:
        title = request.form.get("title", "").strip()
        author = request.form.get("author", "").strip()
        cover_url = request.form.get("cover_url", "").strip() or None
        google_books_id = request.form.get("google_books_id", "").strip() or None

        if not title:
            return '<span class="card-action-feedback">Titre manquant</span>'

        # Find or create book
        book = None
        if google_books_id:
            book = db.query(Book).filter(Book.google_books_id == google_books_id).first()
        if not book:
            book = db.query(Book).filter(Book.title == title).first()
        if not book:
            author_obj = None
            if author:
                author_obj = db.query(Author).filter(Author.name == author).first()
                if not author_obj:
                    author_obj = Author(name=author)
                    db.add(author_obj)
                    db.flush()
            book = Book(
                title=title,
                author_name=author,
                author_id=author_obj.id if author_obj else None,
                google_books_id=google_books_id,
                cover_url=cover_url,
                is_read=False,
            )
            db.add(book)
            db.flush()

        existing = db.query(ReadingListItem).filter(ReadingListItem.book_id == book.id).first()
        if existing:
            return '<span class="card-action-feedback">Deja dans la PAL</span>'

        db.add(ReadingListItem(book_id=book.id))
        db.commit()
        return '<span class="card-action-feedback">Ajoute a la PAL</span>'
    finally:
        db.close()


@plook_app.route("/reco/mark-read", methods=["POST"])
def reco_mark_read():
    db = get_db()
    try:
        title = request.form.get("title", "").strip()
        author = request.form.get("author", "").strip()
        cover_url = request.form.get("cover_url", "").strip() or None
        google_books_id = request.form.get("google_books_id", "").strip() or None

        if not title:
            return '<span class="card-action-feedback">Titre manquant</span>'

        book = None
        if google_books_id:
            book = db.query(Book).filter(Book.google_books_id == google_books_id).first()
        if not book:
            book = db.query(Book).filter(Book.title == title).first()
        if not book:
            author_obj = None
            if author:
                author_obj = db.query(Author).filter(Author.name == author).first()
                if not author_obj:
                    author_obj = Author(name=author)
                    db.add(author_obj)
                    db.flush()
            book = Book(
                title=title,
                author_name=author,
                author_id=author_obj.id if author_obj else None,
                google_books_id=google_books_id,
                cover_url=cover_url,
                is_read=True,
            )
            db.add(book)
        else:
            book.is_read = True

        db.commit()
        return '<span class="card-action-feedback">Marque comme lu</span>'
    finally:
        db.close()


@plook_app.route("/reco/dislike", methods=["POST"])
def reco_dislike():
    db = get_db()
    try:
        title = request.form.get("title", "").strip()
        author = request.form.get("author", "").strip()

        if not title:
            return '<span class="card-action-feedback">Titre manquant</span>'

        existing = db.query(Dislike).filter(Dislike.title == title).first()
        if not existing:
            db.add(Dislike(title=title))
            db.commit()

        return '<span class="card-action-feedback card-action-rejected">Retire des recos</span>'
    finally:
        db.close()


# ==================== PAL actions (HTMX) ====================

@plook_app.route("/pal/remove/<int:item_id>", methods=["DELETE"])
def pal_remove(item_id):
    db = get_db()
    try:
        item = db.query(ReadingListItem).get(item_id)
        if item:
            db.delete(item)
            db.commit()
        return ""
    finally:
        db.close()


@plook_app.route("/pal/mark-read/<int:book_id>", methods=["POST"])
def pal_mark_read(book_id):
    db = get_db()
    try:
        book = db.query(Book).get(book_id)
        if book:
            book.is_read = True
            book.reading_progress = 100
            # Remove from PAL
            pal_item = db.query(ReadingListItem).filter(ReadingListItem.book_id == book_id).first()
            if pal_item:
                db.delete(pal_item)
            db.commit()
        return '<span class="card-action-feedback">Termine -- retire de la PAL</span>'
    finally:
        db.close()


@plook_app.route("/pal/progress/<int:book_id>", methods=["POST"])
def pal_progress(book_id):
    db = get_db()
    try:
        progress = int(request.form.get("progress", 0))
        book = db.query(Book).get(book_id)
        if book:
            book.reading_progress = max(0, min(100, progress))
            db.commit()
        return f'<span class="progress-label">{book.reading_progress}%</span>'
    finally:
        db.close()


@plook_app.route("/search/books", methods=["GET"])
def search_books_route():
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return ""
    try:
        from plook.books_api import search_books
        results = search_books(q, max_results=6)
        return render_template("partials/search_results.html", request=request, results=results)
    except Exception:
        return '<div class="placeholder-section">Erreur de recherche.</div>'


@plook_app.route("/search/add", methods=["POST"])
def search_add():
    db = get_db()
    try:
        google_books_id = request.form.get("google_books_id", "")
        title = request.form.get("title", "")
        author = request.form.get("author", "")
        cover_url = request.form.get("cover_url", "") or None
        synopsis = request.form.get("synopsis", "") or None
        page_count = request.form.get("page_count", "")
        isbn = request.form.get("isbn", "") or None

        # Check if already exists
        existing = None
        if google_books_id:
            existing = db.query(Book).filter(Book.google_books_id == google_books_id).first()
        if not existing and title:
            existing = db.query(Book).filter(Book.title == title).first()

        if existing:
            return f'<div class="tag">Deja dans ta collection</div>'

        # Find or create author
        author_obj = None
        if author:
            author_obj = db.query(Author).filter(Author.name == author).first()
            if not author_obj:
                author_obj = Author(name=author)
                db.add(author_obj)
                db.flush()

        book = Book(
            title=title,
            author_name=author,
            author_id=author_obj.id if author_obj else None,
            google_books_id=google_books_id or None,
            cover_url=cover_url,
            synopsis=synopsis,
            page_count=int(page_count) if page_count and page_count.isdigit() else None,
            isbn=isbn,
            is_read=False,
        )
        db.add(book)
        db.commit()
        return f'<div class="tag">"{title}" ajoute</div>'
    finally:
        db.close()


if __name__ == "__main__":
    plook_app.run(debug=True, port=8001)

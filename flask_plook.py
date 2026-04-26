"""Flask app for Plook (books) — mounted at /plook via WSGI dispatcher."""

from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, jsonify

from plook.database import init_db, SessionLocal
from plook.models import Book, Author, ReadingListItem, Alert, Dislike

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
    return render_template("home.html", request=request)


@plook_app.route("/bibliotheque")
def bibliotheque():
    db = get_db()
    try:
        books = (
            db.query(Book)
            .filter(Book.cover_url.isnot(None))
            .order_by(Book.author_name, Book.title)
            .all()
        )
        return render_template("bibliotheque.html", request=request, books=books)
    finally:
        db.close()


@plook_app.route("/livre/<int:book_id>")
def livre_detail(book_id):
    db = get_db()
    try:
        book = db.query(Book).get(book_id)
        if not book:
            return redirect(url_for("home"))
        return render_template("livre.html", request=request, book=book)
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
            return redirect(url_for("home"))
        books = (
            db.query(Book)
            .filter(Book.author_id == author_id)
            .order_by(Book.year.desc())
            .all()
        )
        return render_template("auteur.html", request=request, author=author, books=books)
    finally:
        db.close()


@plook_app.route("/health")
def health():
    return {"status": "ok", "app": "plook"}


# ==================== HTMX stubs ====================

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
        existing = db.query(ReadingListItem).filter(
            ReadingListItem.book_id == book_id
        ).first()
        if not existing:
            db.add(ReadingListItem(book_id=book_id))
            db.commit()
        return '<div class="tag">Ajoute a la PAL</div>'
    finally:
        db.close()


@plook_app.route("/search/books", methods=["POST"])
def search_books():
    """Google Books search — stub for now."""
    return ""


@plook_app.route("/search/add", methods=["POST"])
def search_add():
    """Add book from search results — stub for now."""
    return ""


if __name__ == "__main__":
    plook_app.run(debug=True, port=8001)

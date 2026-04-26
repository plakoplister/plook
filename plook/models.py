from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship
from plook.database import Base


class Book(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False, index=True)
    author_name = Column(String, index=True)
    author_id = Column(Integer, ForeignKey("authors.id"), nullable=True)
    publisher = Column(String)
    year = Column(Integer)
    isbn = Column(String)
    genre = Column(String)
    is_best = Column(Boolean, default=False)
    is_read = Column(Boolean, default=False)
    reading_progress = Column(Integer, default=0)
    format = Column(String)

    # Google Books enrichment
    google_books_id = Column(String, unique=True)
    cover_url = Column(String)
    synopsis = Column(Text)
    google_categories = Column(JSON)
    google_rating = Column(Float)
    google_rating_count = Column(Integer)
    page_count = Column(Integer)

    # Open Library enrichment
    ol_key = Column(String)
    ol_subjects = Column(JSON)

    added_at = Column(DateTime, default=datetime.utcnow)
    read_at = Column(DateTime, nullable=True)
    score = Column(Integer, nullable=True)
    notes = Column(Text)

    # Store links
    kobo_url = Column(String)
    amazon_url = Column(String)

    author = relationship("Author", back_populates="books")
    reading_list_entries = relationship("ReadingListItem", back_populates="book")

    def __repr__(self):
        return f"<Book {self.title} ({self.author_name})>"


class Author(Base):
    __tablename__ = "authors"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, index=True)
    bio = Column(Text)
    photo_url = Column(String)
    is_followed = Column(Boolean, default=False)
    last_checked = Column(DateTime, nullable=True)

    books = relationship("Book", back_populates="author")

    def __repr__(self):
        return f"<Author {self.name}>"


class ReadingListItem(Base):
    __tablename__ = "reading_list"

    id = Column(Integer, primary_key=True)
    book_id = Column(Integer, ForeignKey("books.id"), nullable=False)
    position = Column(Integer)
    priority = Column(String, default="normal")
    added_at = Column(DateTime, default=datetime.utcnow)

    book = relationship("Book", back_populates="reading_list_entries")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True)
    author_id = Column(Integer, ForeignKey("authors.id"), nullable=False)
    book_title = Column(String)
    book_google_id = Column(String)
    cover_url = Column(String)
    release_date = Column(String)
    seen = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    author = relationship("Author")


class RecoExplanation(Base):
    __tablename__ = "reco_explanations"

    id = Column(Integer, primary_key=True)
    book_title = Column(String)
    explanation = Column(Text)
    reco_type = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class Dislike(Base):
    __tablename__ = "dislikes"

    id = Column(Integer, primary_key=True)
    book_id = Column(Integer, ForeignKey("books.id"), nullable=True)
    google_books_id = Column(String, nullable=True)
    title = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

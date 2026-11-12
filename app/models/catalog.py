import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Table, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class MovieStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


def _link(name: str, other: str, target_table: str) -> Table:
    return Table(
        name,
        Base.metadata,
        Column("movie_id", ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True),
        Column(f"{other}_id", ForeignKey(f"{target_table}.id", ondelete="CASCADE"), primary_key=True),
    )


movie_genres = _link("movie_genres", "genre", "genres")
movie_categories = _link("movie_categories", "category", "categories")
movie_languages = _link("movie_languages", "language", "languages")
movie_countries = _link("movie_countries", "country", "countries")

series_genres = Table(
    "series_genres",
    Base.metadata,
    Column("series_id", ForeignKey("series.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True),
)
series_categories = Table(
    "series_categories",
    Base.metadata,
    Column("series_id", ForeignKey("series.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)
series_languages = Table(
    "series_languages",
    Base.metadata,
    Column("series_id", ForeignKey("series.id", ondelete="CASCADE"), primary_key=True),
    Column("language_id", ForeignKey("languages.id", ondelete="CASCADE"), primary_key=True),
)
series_countries = Table(
    "series_countries",
    Base.metadata,
    Column("series_id", ForeignKey("series.id", ondelete="CASCADE"), primary_key=True),
    Column("country_id", ForeignKey("countries.id", ondelete="CASCADE"), primary_key=True),
)


class _Taxonomy:
    """Shared columns for classification tables (genres, categories, languages, countries)."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(default=True, server_default="1")


class Genre(_Taxonomy, Base):
    __tablename__ = "genres"


class Category(_Taxonomy, Base):
    __tablename__ = "categories"
    image_path: Mapped[str | None] = mapped_column(String(255), default=None)


class Language(_Taxonomy, Base):
    __tablename__ = "languages"


class Country(_Taxonomy, Base):
    __tablename__ = "countries"


class Movie(Base):
    __tablename__ = "movies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    original_title: Mapped[str] = mapped_column(String(200), default="", server_default="")
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    release_year: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    runtime_minutes: Mapped[int | None] = mapped_column(Integer, default=None)
    rating: Mapped[float | None] = mapped_column(Float, default=None)
    imdb_votes: Mapped[int | None] = mapped_column(Integer, default=None)
    metascore: Mapped[int | None] = mapped_column(Integer, default=None)
    box_office: Mapped[str] = mapped_column(String(300), default="", server_default="")
    awards: Mapped[str] = mapped_column(String(300), default="", server_default="")
    # shortcut: people stored as comma-separated text, no person pages or search, upgrade when cast pages are needed
    directors: Mapped[str] = mapped_column(Text, default="", server_default="")
    writers: Mapped[str] = mapped_column(Text, default="", server_default="")
    actors: Mapped[str] = mapped_column(Text, default="", server_default="")
    age_rating: Mapped[str] = mapped_column(String(16), default="", server_default="")
    short_description: Mapped[str] = mapped_column(String(500), default="", server_default="")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    poster_path: Mapped[str | None] = mapped_column(String(255), default=None)
    backdrop_path: Mapped[str | None] = mapped_column(String(255), default=None)
    trailer_url: Mapped[str | None] = mapped_column(String(500), default=None)
    status: Mapped[str] = mapped_column(String(16), default=MovieStatus.DRAFT, server_default=MovieStatus.DRAFT, index=True)
    is_featured: Mapped[bool] = mapped_column(default=False, server_default="0")
    is_exclusive: Mapped[bool] = mapped_column(default=False, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    seo_title: Mapped[str] = mapped_column(String(200), default="", server_default="")
    seo_description: Mapped[str] = mapped_column(String(300), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # selectin loads each relation in one extra query for a whole page of movies, avoiding N+1.
    genres: Mapped[list[Genre]] = relationship(secondary=movie_genres, lazy="selectin", order_by=Genre.name)
    categories: Mapped[list[Category]] = relationship(secondary=movie_categories, lazy="selectin", order_by=Category.name)
    languages: Mapped[list[Language]] = relationship(secondary=movie_languages, lazy="selectin", order_by=Language.name)
    countries: Mapped[list[Country]] = relationship(secondary=movie_countries, lazy="selectin", order_by=Country.name)


class Series(Base):
    __tablename__ = "series"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    original_title: Mapped[str] = mapped_column(String(200), default="", server_default="")
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    release_year: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    rating: Mapped[float | None] = mapped_column(Float, default=None)
    imdb_votes: Mapped[int | None] = mapped_column(Integer, default=None)
    metascore: Mapped[int | None] = mapped_column(Integer, default=None)
    awards: Mapped[str] = mapped_column(String(300), default="", server_default="")
    # shortcut: people stored as comma-separated text, no person pages or search, upgrade when cast pages are needed
    directors: Mapped[str] = mapped_column(Text, default="", server_default="")
    creators: Mapped[str] = mapped_column(Text, default="", server_default="")
    writers: Mapped[str] = mapped_column(Text, default="", server_default="")
    actors: Mapped[str] = mapped_column(Text, default="", server_default="")
    age_rating: Mapped[str] = mapped_column(String(16), default="", server_default="")
    short_description: Mapped[str] = mapped_column(String(500), default="", server_default="")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    poster_path: Mapped[str | None] = mapped_column(String(255), default=None)
    backdrop_path: Mapped[str | None] = mapped_column(String(255), default=None)
    trailer_url: Mapped[str | None] = mapped_column(String(500), default=None)
    status: Mapped[str] = mapped_column(String(16), default=MovieStatus.DRAFT, server_default=MovieStatus.DRAFT, index=True)
    is_featured: Mapped[bool] = mapped_column(default=False, server_default="0")
    is_exclusive: Mapped[bool] = mapped_column(default=False, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    seo_title: Mapped[str] = mapped_column(String(200), default="", server_default="")
    seo_description: Mapped[str] = mapped_column(String(300), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    genres: Mapped[list[Genre]] = relationship(secondary=series_genres, lazy="selectin", order_by=Genre.name)
    categories: Mapped[list[Category]] = relationship(secondary=series_categories, lazy="selectin", order_by=Category.name)
    languages: Mapped[list[Language]] = relationship(secondary=series_languages, lazy="selectin", order_by=Language.name)
    countries: Mapped[list[Country]] = relationship(secondary=series_countries, lazy="selectin", order_by=Country.name)
    seasons: Mapped[list["Season"]] = relationship(back_populates="series", cascade="all, delete-orphan", order_by="Season.number")


class Season(Base):
    __tablename__ = "seasons"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    series_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200), default="", server_default="")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    release_year: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    series: Mapped["Series"] = relationship(back_populates="seasons")
    episodes: Mapped[list["Episode"]] = relationship(back_populates="season", cascade="all, delete-orphan", order_by="Episode.number")


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    season_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    runtime_minutes: Mapped[int | None] = mapped_column(Integer, default=None)
    air_date: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    season: Mapped["Season"] = relationship(back_populates="episodes")


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    content: Mapped[str] = mapped_column(Text)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    movie_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True, default=None)
    series_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True, default=None)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("comments.id", ondelete="CASCADE"), default=None)
    is_approved: Mapped[bool] = mapped_column(default=False, server_default="0", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    author: Mapped["User"] = relationship(foreign_keys=[author_id])


class Favorite(Base):
    __tablename__ = "favorites"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    movie_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True, default=None)
    series_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    movie: Mapped["Movie | None"] = relationship(lazy="selectin")
    series: Mapped["Series | None"] = relationship(lazy="selectin")


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    movie_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True, default=None)
    series_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    movie: Mapped["Movie | None"] = relationship(lazy="selectin")
    series: Mapped["Series | None"] = relationship(lazy="selectin")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    movie_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True, default=None)
    series_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True, default=None)
    rating: Mapped[float] = mapped_column(Float)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    is_approved: Mapped[bool] = mapped_column(default=False, server_default="0", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    author: Mapped["User"] = relationship(foreign_keys=[user_id])

"""
db_client.py
------------
Database layer for Meli Trends using SQLAlchemy + Neon Serverless Postgres.

Responsibilities:
  - Engine / session factory creation (singleton via module-level state)
  - ORM model definition for ``trend_history``
  - Table initialization (create-if-not-exists)
  - Bulk-insert of a trend snapshot from a Pandas DataFrame
  - Query helpers for historical time-series data
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    create_engine,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

DATABASE_URL: str = os.getenv("DATABASE_URL", "")


# ---------------------------------------------------------------------------
# SQLAlchemy setup
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class TrendHistory(Base):
    """ORM model for the ``trend_history`` table.

    Each row captures a single keyword's rank for a given category at a
    specific point in time, forming a time-series of trend snapshots.

    Columns:
        id:          Auto-incrementing surrogate primary key.
        category_id: Mercado Livre category identifier (e.g. ``"MLB1051"``).
        keyword:     The trending search term.
        rank:        1-based rank at the time of capture (lower = more popular).
        created_at:  UTC timestamp when this snapshot row was inserted.
    """

    __tablename__ = "trend_history"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    category_id: str = Column(String(64), nullable=False, index=True)
    keyword: str = Column(String(512), nullable=False)
    rank: int = Column(Integer, nullable=False)
    created_at: datetime = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TrendHistory id={self.id} category={self.category_id!r} "
            f"keyword={self.keyword!r} rank={self.rank} at={self.created_at}>"
        )


class ItemInsight(Base):
    """ORM model for caching AI insights for products.

    Columns:
        item_id:    Mercado Livre Item ID (e.g., \"MLB123456\"). Primary Key.
        insights:   The generated markdown insights text.
        created_at: UTC timestamp of generation. Used for 24h TTL.
    """

    __tablename__ = "item_insight"

    item_id: str = Column(String(64), primary_key=True)
    insights: str = Column(String, nullable=False)
    created_at: datetime = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ItemInsight item_id={self.item_id!r} at={self.created_at}>"


# ---------------------------------------------------------------------------
# Engine / session factory (module-level singleton)
# ---------------------------------------------------------------------------

_engine = None
_SessionLocal = None


def _get_engine():
    """Return the SQLAlchemy engine, creating it on first call."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        if not DATABASE_URL:
            raise ValueError(
                "DATABASE_URL is not set. Add it to your .env file."
            )
        _engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,   # validates connections before use (important for Neon's serverless cold starts)
            pool_size=2,
            max_overflow=5,
        )
    return _engine


def _get_session_factory() -> sessionmaker:
    """Return the session factory, creating it on first call."""
    global _SessionLocal  # noqa: PLW0603
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=_get_engine(), autocommit=False, autoflush=False
        )
    return _SessionLocal


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_tables() -> None:
    """Create all ORM-managed tables if they do not already exist.

    Safe to call on every app startup — ``checkfirst=True`` is the default
    behaviour of ``Base.metadata.create_all``, so existing tables are never
    dropped or truncated.

    Raises:
        ValueError: If ``DATABASE_URL`` is not configured.
        sqlalchemy.exc.OperationalError: On connection failure.
    """
    Base.metadata.create_all(bind=_get_engine())


def save_trends_to_db(category_id: str, df_trends: pd.DataFrame) -> int:
    """Persist a trends snapshot to the ``trend_history`` table.

    Each row in *df_trends* becomes one ``TrendHistory`` record stamped with
    the current UTC time. Rows without a ``keyword`` column are silently
    skipped.

    Args:
        category_id: The MLB category identifier the trends belong to.
        df_trends:   DataFrame produced by ``app.load_trends()``. Must contain
                     at least ``rank`` and ``keyword`` columns.

    Returns:
        int: Number of rows inserted.

    Raises:
        ValueError: If ``DATABASE_URL`` is not configured or the DataFrame
                    is missing required columns.
        sqlalchemy.exc.SQLAlchemyError: On any database error (rolled back
                                         automatically).
    """
    if df_trends.empty:
        return 0

    required_cols = {"rank", "keyword"}
    missing = required_cols - set(df_trends.columns)
    if missing:
        raise ValueError(
            f"df_trends is missing required columns: {missing}"
        )

    snapshot_ts = datetime.now(timezone.utc)

    records = [
        TrendHistory(
            category_id=category_id,
            keyword=row["keyword"],
            rank=int(row["rank"]),
            created_at=snapshot_ts,
        )
        for _, row in df_trends[["rank", "keyword"]].iterrows()
    ]

    SessionLocal = _get_session_factory()
    with SessionLocal() as session:
        try:
            session.add_all(records)
            session.commit()
        except Exception:
            session.rollback()
            raise

    return len(records)


def get_trend_history(
    category_id: str,
    limit: int = 500,
) -> pd.DataFrame:
    """Query historical trend snapshots for a given category.

    Args:
        category_id: The MLB category identifier to filter by.
        limit:       Maximum number of rows to return (most recent first).

    Returns:
        pd.DataFrame with columns ``id``, ``category_id``, ``keyword``,
        ``rank``, and ``created_at``. Empty DataFrame if no data exists.

    Raises:
        ValueError: If ``DATABASE_URL`` is not configured.
        sqlalchemy.exc.SQLAlchemyError: On query failure.
    """
    stmt = (
        select(TrendHistory)
        .where(TrendHistory.category_id == category_id)
        .order_by(TrendHistory.created_at.desc())
        .limit(limit)
    )

    SessionLocal = _get_session_factory()
    with SessionLocal() as session:
        rows = session.execute(stmt).scalars().all()

    if not rows:
        return pd.DataFrame(
            columns=["id", "category_id", "keyword", "rank", "created_at"]
        )

    return pd.DataFrame(
        [
            {
                "id": r.id,
                "category_id": r.category_id,
                "keyword": r.keyword,
                "rank": r.rank,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    )


def get_keyword_rank_over_time(
    category_id: str,
    top_n: int = 10,
) -> pd.DataFrame:
    """Build a pivot-ready DataFrame of rank-over-time for the top keywords.

    Selects the *top_n* keywords (by lowest average rank across all snapshots)
    and returns every historical rank data-point for them, suitable for
    plotting a time-series chart.

    Args:
        category_id: The MLB category identifier.
        top_n:       Number of top keywords to include.

    Returns:
        pd.DataFrame with columns ``created_at``, ``keyword``, ``rank``.
        Empty DataFrame if no history exists.
    """
    df = get_trend_history(category_id, limit=5000)
    if df.empty:
        return df

    # Identify the top_n keywords by their best (lowest) average rank
    top_keywords: list[str] = (
        df.groupby("keyword")["rank"]
        .mean()
        .nsmallest(top_n)
        .index.tolist()
    )

    return (
        df[df["keyword"].isin(top_keywords)][["created_at", "keyword", "rank"]]
        .sort_values("created_at")
        .reset_index(drop=True)
    )


def get_item_insight(item_id: str) -> Optional[str]:
    """Retrieve unexpired AI insight for an item.
    
    Returns the insight markdown string if it exists and is younger than 24 hours.
    Otherwise returns None.
    """
    stmt = select(ItemInsight).where(ItemInsight.item_id == item_id)
    SessionLocal = _get_session_factory()
    with SessionLocal() as session:
        insight = session.execute(stmt).scalar_one_or_none()
    
    if insight:
        age = datetime.now(timezone.utc) - insight.created_at
        if age <= timedelta(hours=24):
            return insight.insights
    return None


def save_item_insight(item_id: str, insights: str) -> None:
    """Save or update an AI insight for an item."""
    SessionLocal = _get_session_factory()
    with SessionLocal() as session:
        try:
            insight = session.execute(
                select(ItemInsight).where(ItemInsight.item_id == item_id)
            ).scalar_one_or_none()
            
            if insight:
                insight.insights = insights
                insight.created_at = datetime.now(timezone.utc)
            else:
                insight = ItemInsight(
                    item_id=item_id,
                    insights=insights,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(insight)
            session.commit()
        except Exception:
            session.rollback()
            raise

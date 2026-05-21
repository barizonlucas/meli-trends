"""
app.py
------
Streamlit dashboard for Meli Trends.

Displays top trending searches and products per category on Mercado Livre
Brazil (MLB). Authentication is handled via the OAuth 2.0 authorization code
flow with a manual code paste (since we run locally without a callback server).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from requests import HTTPError
from sqlalchemy.exc import SQLAlchemyError

import db_client as db
import meli_client as mc

# ---------------------------------------------------------------------------
# DB initialization (runs once per Streamlit process)
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="Connecting to database…")
def _init_db() -> bool:
    """Create tables if they don't exist. Returns True on success."""
    try:
        db.create_tables()
        return True
    except Exception as exc:  # noqa: BLE001
        st.warning(f"⚠️ Database unavailable — history will not be saved. ({exc})")
        return False

_db_ready: bool = _init_db()

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Meli Trends",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------

if "access_token" not in st.session_state:
    st.session_state.access_token = None

if "token_info" not in st.session_state:
    st.session_state.token_info = {}

# ---------------------------------------------------------------------------
# Sidebar — Authentication
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("🛒 Meli Trends")
    st.caption("Powered by the Mercado Livre API")
    st.divider()

    # ── Auth section ─────────────────────────────────────────────────────────
    st.subheader("🔐 Authentication")

    if st.session_state.access_token:
        user_id = st.session_state.token_info.get("user_id", "—")
        st.success(f"Authenticated ✅\n\nUser ID: `{user_id}`")
        if st.button("Sign out", use_container_width=True):
            st.session_state.access_token = None
            st.session_state.token_info = {}
            st.rerun()
    else:
        # Step 1 — Generate and display the authorization URL
        try:
            auth_url = mc.get_authorization_url()
            st.markdown(
                "**Step 1.** Click the link below to authorize the app in your "
                "browser:"
            )
            st.markdown(f"[🔗 Authorize on Mercado Livre]({auth_url})")
        except ValueError as exc:
            st.error(f"Configuration error: {exc}")
            st.stop()

        # Step 2 — Paste the code from the redirect URL
        st.markdown(
            "**Step 2.** After authorizing, you'll be redirected to "
            f"`{mc.REDIRECT_URI}?code=...`. Copy the `code` value and paste "
            "it below:"
        )
        auth_code = st.text_input(
            "Authorization code",
            placeholder="Paste your code here…",
            label_visibility="collapsed",
        )

        if st.button("🔑 Exchange for Access Token", use_container_width=True):
            if not auth_code.strip():
                st.warning("Please paste the authorization code first.")
            else:
                with st.spinner("Exchanging code for token…"):
                    try:
                        token_data = mc.exchange_code_for_token(
                            auth_code.strip()
                        )
                        st.session_state.access_token = token_data["access_token"]
                        st.session_state.token_info = token_data
                        st.success("Token obtained successfully!")
                        st.rerun()
                    except HTTPError as exc:
                        st.error(
                            f"Token exchange failed: {exc.response.status_code} "
                            f"— {exc.response.text}"
                        )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Unexpected error: {exc}")

    st.divider()

    # ── Category selector ─────────────────────────────────────────────────────
    st.subheader("📂 Category")

    @st.cache_data(ttl=3600, show_spinner="Loading categories…")
    def load_categories() -> list[dict]:
        return mc.fetch_categories()

    try:
        categories = load_categories()
    except HTTPError as exc:
        st.error(f"Could not load categories: {exc}")
        st.stop()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Unexpected error loading categories: {exc}")
        st.stop()

    category_map: dict[str, str] = {cat["name"]: cat["id"] for cat in categories}
    selected_name = st.selectbox(
        "Select a category",
        options=list(category_map.keys()),
        index=0,
        label_visibility="collapsed",
    )
    selected_id: str = category_map[selected_name]

    st.caption(f"Category ID: `{selected_id}`")

# ---------------------------------------------------------------------------
# Helper — data processing
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300, show_spinner="Fetching trends…")
def load_trends(category_id: str, access_token: str | None) -> pd.DataFrame:
    """Fetch trends for *category_id* and return a clean DataFrame.

    The result is cached for 5 minutes. After the first (uncached) call the
    snapshot is automatically persisted to the database.

    Args:
        category_id: MLB category identifier.
        access_token: Optional OAuth access token.

    Returns:
        pd.DataFrame with columns ``rank``, ``keyword`` (and ``url`` when
        available).
    """
    raw: list[dict] = mc.fetch_trends(category_id, access_token)

    if not raw:
        return pd.DataFrame(columns=["rank", "keyword"])

    df = pd.DataFrame(raw)

    # The trends endpoint returns a list of objects with at least ``keyword``.
    # Normalize column names to snake_case for consistency.
    df.columns = [col.lower().replace(" ", "_") for col in df.columns]

    # Add a 1-based rank column
    df.insert(0, "rank", range(1, len(df) + 1))

    return df


def _persist_trends(category_id: str, df: pd.DataFrame) -> None:
    """Save *df* to the DB when a fresh (uncached) fetch occurred.

    Failures are surfaced as non-blocking Streamlit toasts so the main UI
    is never interrupted by persistence errors.
    """
    if not _db_ready or df.empty:
        return
    try:
        n = db.save_trends_to_db(category_id, df)
        st.toast(f"💾 {n} trend rows saved to history.", icon="✅")
    except (SQLAlchemyError, ValueError) as exc:
        st.toast(f"DB write failed: {exc}", icon="⚠️")


# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

st.title("📈 Meli Trends")
st.markdown(
    f"Showing top trending searches on **Mercado Livre Brazil** for "
    f"**{selected_name}**."
)
st.divider()

# ── Fetch & display trends ────────────────────────────────────────────────────
_cache_info_before = load_trends.cache_info() if hasattr(load_trends, "cache_info") else None

try:
    df_trends = load_trends(selected_id, st.session_state.access_token)
except HTTPError as exc:
    st.error(
        f"Could not fetch trends: {exc.response.status_code} — {exc.response.text}"
    )
    st.stop()
except Exception as exc:  # noqa: BLE001
    st.error(f"Unexpected error: {exc}")
    st.stop()

# Persist on every fresh (uncached) load — Streamlit's cache_data does not
# expose cache hit/miss natively, so we persist unconditionally on the first
# render and rely on the TTL to suppress redundant writes on re-renders.
# A production system would track snapshot timestamps in the DB itself.
if "last_persisted" not in st.session_state:
    st.session_state.last_persisted = {}

_persist_key = f"{selected_id}_{st.session_state.access_token}"
if st.session_state.last_persisted.get(_persist_key) is None:
    _persist_trends(selected_id, df_trends)
    st.session_state.last_persisted[_persist_key] = True

if df_trends.empty:
    st.info("No trends found for this category.")
    st.stop()

# ── Layout — two columns ──────────────────────────────────────────────────────
col_table, col_chart = st.columns([1.2, 1], gap="large")

with col_table:
    st.subheader(f"🔥 Top {len(df_trends)} Trends")

    # Display columns: always show rank + keyword; show url only if present
    display_cols = ["rank", "keyword"]
    if "url" in df_trends.columns:
        display_cols.append("url")

    st.dataframe(
        df_trends[display_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "rank": st.column_config.NumberColumn("Rank", width="small"),
            "keyword": st.column_config.TextColumn("Keyword", width="large"),
            **(
                {
                    "url": st.column_config.LinkColumn(
                        "Link", display_text="Open ↗", width="small"
                    )
                }
                if "url" in df_trends.columns
                else {}
            ),
        },
    )

with col_chart:
    st.subheader("📊 Trend Ranking")
    st.caption(
        "Bar length = inverse rank (top keyword = longest bar). "
        "Quantitative volume data is not exposed by this endpoint."
    )

    # Build a simple score based on inverse rank so the chart is meaningful
    chart_df = (
        df_trends[["rank", "keyword"]]
        .assign(score=lambda d: len(d) - d["rank"] + 1)
        .set_index("keyword")[["score"]]
        .sort_values("score", ascending=True)
    )

    st.bar_chart(chart_df, horizontal=True, use_container_width=True)

# ── Deep Dive: Top Sellers ────────────────────────────────────────────────────
st.divider()
st.subheader("🛍️ Deep Dive: Top Sellers")
st.caption(
    f"Best-selling products on Mercado Livre for **{selected_name}** · "
    "Sourced from the Highlights API · Cached 15 min."
)


@st.cache_data(ttl=900, show_spinner="Loading top sellers…")
def load_highlights(category_id: str, access_token: str | None) -> list[dict]:
    """Cached wrapper around :func:`meli_client.fetch_category_highlights`.

    Args:
        category_id:  MLB category identifier.
        access_token: Optional user OAuth token (falls back to app token).

    Returns:
        list[dict]: Up to 4 product dicts (title, price, permalink, thumbnail).
    """
    return mc.fetch_category_highlights(
        category_id, access_token=access_token, limit=4
    )


try:
    highlights = load_highlights(selected_id, st.session_state.access_token)
except HTTPError as exc:
    st.warning(
        f"Could not load highlights ({exc.response.status_code}). "
        "The Highlights API may not have data for this category yet."
    )
    highlights = []
except Exception as exc:  # noqa: BLE001
    st.warning(f"Unexpected error loading highlights: {exc}")
    highlights = []

if not highlights:
    st.info(
        "No best-seller data available for this category. "
        "Try selecting a high-traffic category such as **Tecnologia** or **Moda**."
    )
else:
    prod_cols = st.columns(len(highlights), gap="large")
    for col, product in zip(prod_cols, highlights):
        with col:
            # ── Product image ────────────────────────────────────────────────
            if product["thumbnail"]:
                st.image(product["thumbnail"], use_container_width=True)

            # ── Title (truncate at 72 chars to keep cards tidy) ──────────────
            title: str = product["title"]
            display_title = title if len(title) <= 72 else title[:69] + "…"
            st.markdown(f"**{display_title}**")

            # ── Price in BRL (Brazilian thousand/decimal separators) ──────────
            price: float = product["price"]
            # Python formats 1234567.89 as "1,234,567.89" → swap to "1.234.567,89"
            brl = f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            st.markdown(f"💰 **R$ {brl}**")

            # ── Permalink ────────────────────────────────────────────────────
            if product["permalink"]:
                st.markdown(
                    f"[🛒 Ver no Mercado Livre]({product['permalink']})",
                    unsafe_allow_html=False,
                )

# ── Historical data ───────────────────────────────────────────────────────────
st.divider()
with st.expander("🕐 Historical Data — See trend evolution over time", expanded=False):
    if not _db_ready:
        st.warning("Database is not available. Configure `DATABASE_URL` in your `.env` file.")
    else:
        @st.cache_data(ttl=60, show_spinner="Loading history…")
        def load_history(category_id: str) -> pd.DataFrame:
            return db.get_trend_history(category_id)

        @st.cache_data(ttl=60, show_spinner="Building time-series…")
        def load_rank_over_time(category_id: str, top_n: int) -> pd.DataFrame:
            return db.get_keyword_rank_over_time(category_id, top_n=top_n)

        df_history = load_history(selected_id)

        if df_history.empty:
            st.info(
                "No historical data yet for this category. Snapshots are saved "
                "automatically each time you load this page."
            )
        else:
            total_snapshots = df_history["created_at"].nunique()
            first_seen = df_history["created_at"].min()
            last_seen = df_history["created_at"].max()

            m1, m2, m3 = st.columns(3)
            m1.metric("Total rows stored", f"{len(df_history):,}")
            m2.metric("Snapshots captured", total_snapshots)
            m3.metric("Tracking since", first_seen.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(first_seen) else "—")

            st.markdown("##### Raw snapshot log")
            st.dataframe(
                df_history[["created_at", "rank", "keyword"]],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "created_at": st.column_config.DatetimeColumn("Captured At (UTC)", format="YYYY-MM-DD HH:mm"),
                    "rank": st.column_config.NumberColumn("Rank", width="small"),
                    "keyword": st.column_config.TextColumn("Keyword"),
                },
            )

            if total_snapshots >= 2:
                st.markdown("##### Rank over time — top 10 keywords")
                st.caption(
                    "Lower rank = more popular. Each point is one captured snapshot."
                )
                top_n_slider = st.slider(
                    "Keywords to display", min_value=3, max_value=20, value=10, step=1
                )
                df_rot = load_rank_over_time(selected_id, top_n_slider)
                if not df_rot.empty:
                    pivot = (
                        df_rot.pivot_table(
                            index="created_at",
                            columns="keyword",
                            values="rank",
                            aggfunc="min",
                        )
                        .sort_index()
                    )
                    # Invert rank so that rank=1 appears at the top of the chart
                    max_rank = pivot.max().max()
                    pivot_inverted = (max_rank - pivot + 1).fillna(method="ffill")  # type: ignore[arg-type]
                    st.line_chart(pivot_inverted, use_container_width=True)
            else:
                st.info("Come back after a few more refreshes to see the rank-over-time chart (needs ≥ 2 snapshots).")

# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "Data sourced from the [Mercado Livre Trends API](https://developers.mercadolivre.com.br). "
    "Trend scores are derived from keyword ranking — actual search volume is not available "
    "via this endpoint."
)

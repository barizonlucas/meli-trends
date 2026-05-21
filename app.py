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
# i18n — translations dictionary
# ---------------------------------------------------------------------------

LANG_DICT: dict[str, dict[str, str]] = {
    "PT": {
        # Sidebar
        "sidebar_title":          "🛒 Meli Trends",
        "sidebar_caption":        "Desenvolvido com a API do Mercado Livre by Bariza.dev",
        "auth_header":            "🔐 Autenticação",
        "auth_step1":             "**Passo 1.** Clique no link abaixo para autorizar o app no seu navegador:",
        "auth_link":              "🔗 Autorizar no Mercado Livre",
        "auth_step2_pre":         "**Passo 2.** Após autorizar, você será redirecionado para",
        "auth_code_placeholder":  "Cole seu código aqui…",
        "auth_button":            "🔑 Trocar pelo Access Token",
        "auth_paste_warning":     "Por favor, cole o código de autorização primeiro.",
        "auth_success":           "Token obtido com sucesso!",
        "auth_signout":           "Sair",
        "auth_authenticated":     "Autenticado ✅",
        "category_header":        "📂 Categoria",
        "category_select_label":  "Selecione uma categoria",
        # Main area
        "page_title":             "📈 Meli Trends",
        "page_subtitle":          "Exibindo as buscas mais populares no **Mercado Livre Brasil** para",
        "trends_header":          "🔥 Top Tendências",
        "chart_header":           "📊 Ranking de Tendências",
        "chart_caption":          "Comprimento da barra = rank inverso (keyword #1 = barra maior). Volume real não é exposto por este endpoint.",
        "col_rank":               "Rank",
        "col_keyword":            "Keyword",
        "col_link":               "Link",
        "link_open":              "Abrir ↗",
        "sellers_header":         "🛍️ Destaque: Mais Vendidos",
        "sellers_caption":        "Produtos mais vendidos no Mercado Livre para **{category}** · API de Destaques · Cache 15 min.",
        "sellers_no_data":        "Nenhum dado de mais vendidos para esta categoria. Tente **Tecnologia**, **Celulares** ou **Moda**.",
        "sellers_link":           "🛒 Ver no Mercado Livre",
        "history_expander":       "🕐 Dados Históricos — Veja a evolução das tendências ao longo do tempo",
        "history_no_db":          "Banco de dados indisponível. Configure `DATABASE_URL` no seu arquivo `.env`.",
        "history_no_data":        "Nenhum dado histórico ainda para esta categoria. Snapshots são salvos automaticamente a cada carregamento.",
        "history_metric_rows":    "Total de linhas salvas",
        "history_metric_snaps":   "Snapshots capturados",
        "history_metric_since":   "Rastreando desde",
        "history_raw_header":     "##### Log de snapshots brutos",
        "history_chart_header":   "##### Rank ao longo do tempo — top keywords",
        "history_chart_caption":  "Rank menor = mais popular. Cada ponto é um snapshot.",
        "history_slider_label":   "Keywords a exibir",
        "history_need_more":      "Volte após mais alguns recarregamentos para ver o gráfico de rank ao longo do tempo (precisa de ≥ 2 snapshots).",
        "col_captured_at":        "Capturado em (UTC)",
        "footer_caption":         "Dados da [API de Tendências do Mercado Livre](https://developers.mercadolivre.com.br). Scores derivados do ranking de keywords — volume real não disponível por este endpoint.",
        "no_trends":              "Nenhuma tendência encontrada para esta categoria.",
        # Tooltips — value-added context for each data view
        "tooltip_top_trends":     "Foco na descoberta: identifique sinais de mercado. Veja as buscas que mais crescem para antecipar demanda e tendências.",
        "tooltip_ranking":        "Visualize a intensidade do interesse: entenda a concentração de mercado. Ideal para priorizar palavras-chave em estratégias de SEO e anúncios.",
        "tooltip_top_sellers":    "Benchmark competitivo: analise líderes de vendas. Compare posicionamento e otimize suas listagens para maior conversão.",
        "tooltip_history":        "Valide padrões ao longo do tempo: detecte sazonalidade e tendências de longo prazo para embasar previsões e estoque.",
    },
    "EN": {
        # Sidebar
        "sidebar_title":          "🛒 Meli Trends",
        "sidebar_caption":        "Powered by the Mercado Livre API by Bariza.dev",
        "auth_header":            "🔐 Authentication",
        "auth_step1":             "**Step 1.** Click the link below to authorize the app in your browser:",
        "auth_link":              "🔗 Authorize on Mercado Livre",
        "auth_step2_pre":         "**Step 2.** After authorizing, you'll be redirected to",
        "auth_code_placeholder":  "Paste your code here…",
        "auth_button":            "🔑 Exchange for Access Token",
        "auth_paste_warning":     "Please paste the authorization code first.",
        "auth_success":           "Token obtained successfully!",
        "auth_signout":           "Sign out",
        "auth_authenticated":     "Authenticated ✅",
        "category_header":        "📂 Category",
        "category_select_label":  "Select a category",
        # Main area
        "page_title":             "📈 Meli Trends",
        "page_subtitle":          "Showing top trending searches on **Mercado Livre Brazil** for",
        "trends_header":          "🔥 Top Trends",
        "chart_header":           "📊 Trend Ranking",
        "chart_caption":          "Bar length = inverse rank (top keyword = longest bar). Quantitative volume data is not exposed by this endpoint.",
        "col_rank":               "Rank",
        "col_keyword":            "Keyword",
        "col_link":               "Link",
        "link_open":              "Open ↗",
        "sellers_header":         "🛍️ Deep Dive: Top Sellers",
        "sellers_caption":        "Best-selling products on Mercado Livre for **{category}** · Highlights API · Cached 15 min.",
        "sellers_no_data":        "No best-seller data for this category. Try **Technology**, **Phones** or **Fashion**.",
        "sellers_link":           "🛒 View on Mercado Livre",
        "history_expander":       "🕐 Historical Data — See trend evolution over time",
        "history_no_db":          "Database is not available. Configure `DATABASE_URL` in your `.env` file.",
        "history_no_data":        "No historical data yet for this category. Snapshots are saved automatically each time you load this page.",
        "history_metric_rows":    "Total rows stored",
        "history_metric_snaps":   "Snapshots captured",
        "history_metric_since":   "Tracking since",
        "history_raw_header":     "##### Raw snapshot log",
        "history_chart_header":   "##### Rank over time — top keywords",
        "history_chart_caption":  "Lower rank = more popular. Each point is one captured snapshot.",
        "history_slider_label":   "Keywords to display",
        "history_need_more":      "Come back after a few more refreshes to see the rank-over-time chart (needs ≥ 2 snapshots).",
        "col_captured_at":        "Captured At (UTC)",
        "footer_caption":         "Data sourced from the [Mercado Livre Trends API](https://developers.mercadolivre.com.br). Trend scores are derived from keyword ranking — actual search volume is not available via this endpoint.",
        "no_trends":              "No trends found for this category.",
        # Tooltips — value-added context for each data view
        "tooltip_top_trends":     "Focus on discovery: identify strong market signals. See the fastest-growing searches to anticipate demand and emerging trends.",
        "tooltip_ranking":        "Visualize interest intensity: understand market concentration. Ideal for prioritizing keywords in your SEO and ad strategies.",
        "tooltip_top_sellers":    "Competitive benchmarking: analyze top-selling products. Compare positioning and optimize your own listings for higher conversion.",
        "tooltip_history":        "Validate patterns over time: detect seasonality and long-term trends to support better forecasting and inventory decisions.",
    },
}

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
    # ── Language selector (must be first so all subsequent strings use it) ────
    lang: str = st.selectbox(
        "🌍 Language / Idioma",
        options=["PT", "EN"],
        index=0,
        key="lang_select",
    )
    T = LANG_DICT[lang]  # shorthand — T["key"] throughout the file

    st.title(T["sidebar_title"])
    st.caption(T["sidebar_caption"])

# ── Category selector ─────────────────────────────────────────────────────
    st.subheader(T["category_header"])

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
        T["category_select_label"],
        options=list(category_map.keys()),
        index=0,
        label_visibility="collapsed",
    )
    selected_id: str = category_map[selected_name]

    st.caption(f"Category ID: `{selected_id}`")

    st.divider()

    # ── Auth section ─────────────────────────────────────────────────────────
    st.subheader(T["auth_header"])

    if st.session_state.access_token:
        user_id = st.session_state.token_info.get("user_id", "—")
        st.success(f"{T['auth_authenticated']}\n\nUser ID: `{user_id}`")
        if st.button(T["auth_signout"], use_container_width=True):
            st.session_state.access_token = None
            st.session_state.token_info = {}
            st.rerun()
    else:
        # Step 1 — Generate and display the authorization URL
        try:
            auth_url = mc.get_authorization_url()
            st.markdown(T["auth_step1"])
            st.markdown(f"[{T['auth_link']}]({auth_url})")
        except ValueError as exc:
            st.error(f"Configuration error: {exc}")
            st.stop()

        # Step 2 — Paste the code from the redirect URL
        st.markdown(
            f"{T['auth_step2_pre']} "
            f"`{mc.REDIRECT_URI}?code=...`. "
            + ("Cole o valor de `code` abaixo:" if lang == "PT" else "Copy the `code` value and paste it below:")
        )
        auth_code = st.text_input(
            "Authorization code",
            placeholder=T["auth_code_placeholder"],
            label_visibility="collapsed",
        )

        if st.button(T["auth_button"], use_container_width=True):
            if not auth_code.strip():
                st.warning(T["auth_paste_warning"])
            else:
                with st.spinner("Exchanging code for token…"):
                    try:
                        token_data = mc.exchange_code_for_token(
                            auth_code.strip()
                        )
                        st.session_state.access_token = token_data["access_token"]
                        st.session_state.token_info = token_data
                        st.success(T["auth_success"])
                        st.rerun()
                    except HTTPError as exc:
                        st.error(
                            f"Token exchange failed: {exc.response.status_code} "
                            f"— {exc.response.text}"
                        )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Unexpected error: {exc}")

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

# ── Global CSS injection ──────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    /* Uniform product card images */
    [data-testid="stImage"] img {
        height: 200px;
        object-fit: contain;
        background-color: #f9f9f9;
        border-radius: 10px;
        padding: 5px;
    }
    /* Flex column layout so cards align even with varying title lengths */
    .stColumn {
        display: flex;
        flex-direction: column;
        justify-content: space-between;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title(T["page_title"])
st.markdown(f"{T['page_subtitle']} **{selected_name}**.")
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
    st.info(T["no_trends"])
    st.stop()

# ── Layout — two columns ──────────────────────────────────────────────────────
col_table, col_chart = st.columns([1.2, 1], gap="large")

with col_table:
    st.subheader(
        f"{T['trends_header']} ({len(df_trends)})",
        help=T["tooltip_top_trends"]
    )

    # Display columns: always show rank + keyword; show url only if present
    display_cols = ["rank", "keyword"]
    if "url" in df_trends.columns:
        display_cols.append("url")

    st.dataframe(
        df_trends[display_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "rank": st.column_config.NumberColumn(T["col_rank"], width="small"),
            "keyword": st.column_config.TextColumn(T["col_keyword"], width="large"),
            **(
                {
                    "url": st.column_config.LinkColumn(
                        T["col_link"], display_text=T["link_open"], width="small"
                    )
                }
                if "url" in df_trends.columns
                else {}
            ),
        },
    )

with col_chart:
    st.subheader(T["chart_header"], help=T["tooltip_ranking"])
    st.caption(T["chart_caption"])

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
st.subheader(T["sellers_header"], help=T["tooltip_top_sellers"])
st.caption(T["sellers_caption"].format(category=selected_name))


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
    st.info(T["sellers_no_data"])
else:
    prod_cols = st.columns(len(highlights), gap="large")
    for col, product in zip(prod_cols, highlights):
        with col:
            # ── Product image (CSS ensures uniform 200px height) ──────────────
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
                    f"[{T['sellers_link']}]({product['permalink']})",
                    unsafe_allow_html=False,
                )

# ── Historical data ───────────────────────────────────────────────────────────
st.divider()
with st.expander(T["history_expander"], expanded=False):
    if not _db_ready:
        st.warning(T["history_no_db"])
    else:
        @st.cache_data(ttl=60, show_spinner="Loading history…")
        def load_history(category_id: str) -> pd.DataFrame:
            return db.get_trend_history(category_id)

        @st.cache_data(ttl=60, show_spinner="Building time-series…")
        def load_rank_over_time(category_id: str, top_n: int) -> pd.DataFrame:
            return db.get_keyword_rank_over_time(category_id, top_n=top_n)

        df_history = load_history(selected_id)

        if df_history.empty:
            st.info(T["history_no_data"])
        else:
            total_snapshots = df_history["created_at"].nunique()
            first_seen = df_history["created_at"].min()

            m1, m2, m3 = st.columns(3)
            m1.metric(T["history_metric_rows"], f"{len(df_history):,}")
            m2.metric(T["history_metric_snaps"], total_snapshots, help=T["tooltip_history"])
            m3.metric(
                T["history_metric_since"],
                first_seen.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(first_seen) else "—",
            )

            st.markdown(T["history_raw_header"])
            st.dataframe(
                df_history[["created_at", "rank", "keyword"]],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "created_at": st.column_config.DatetimeColumn(
                        T["col_captured_at"], format="YYYY-MM-DD HH:mm"
                    ),
                    "rank": st.column_config.NumberColumn(T["col_rank"], width="small"),
                    "keyword": st.column_config.TextColumn(T["col_keyword"]),
                },
            )

            if total_snapshots >= 2:
                st.markdown(T["history_chart_header"])
                st.caption(T["history_chart_caption"])
                top_n_slider = st.slider(
                    T["history_slider_label"], min_value=3, max_value=20, value=10, step=1
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
                    # Fix 1: coerce all values to numeric (handles mixed types)
                    pivot = pivot.apply(pd.to_numeric, errors="coerce")
                    # Fix 2: safe max — guard against all-NaN pivot
                    max_rank = pivot.max().max() if not pivot.empty else 10
                    # Fix 3: modern ffill() — no deprecated fillna(method=...) call
                    pivot_inverted = (max_rank - pivot + 1).ffill()
                    st.line_chart(pivot_inverted, use_container_width=True)
            else:
                st.info(T["history_need_more"])

# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption(T["footer_caption"])

"""
meli_client.py
--------------
Handles all interactions with the Mercado Livre API:
  - OAuth 2.0 authorization URL generation
  - Authorization code exchange for access_token
  - App-level token (client credentials) for unauthenticated browsing
  - Category listing
  - Trend fetching per category
  - Category highlight products (Best Sellers via Highlights + Multi-get Items)
"""

import os
from typing import Optional
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

APP_ID: str = os.getenv("APP_ID", "")
SECRET_KEY: str = os.getenv("SECRET_KEY", "")
REDIRECT_URI: str = os.getenv("REDIRECT_URI", "https://melitrends.bariza.dev")

BASE_AUTH_URL: str = "https://auth.mercadolivre.com.br/authorization"
TOKEN_URL: str = "https://api.mercadolibre.com/oauth/token"
API_BASE_URL: str = "https://api.mercadolibre.com"

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def get_authorization_url() -> str:
    """Build and return the Mercado Livre OAuth 2.0 authorization URL.

    The user must open this URL in their browser, authorize the app, and
    then paste the ``code`` query parameter from the redirect URL back into
    the Streamlit sidebar.

    Returns:
        str: The full authorization URL.

    Raises:
        ValueError: If APP_ID or REDIRECT_URI are not set.
    """
    if not APP_ID:
        raise ValueError(
            "APP_ID is not set. Please configure it in your .env file."
        )
    if not REDIRECT_URI:
        raise ValueError(
            "REDIRECT_URI is not set. Please configure it in your .env file."
        )

    params = {
        "response_type": "code",
        "client_id": APP_ID,
        "redirect_uri": REDIRECT_URI,
    }
    return f"{BASE_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> dict:
    """Exchange an authorization code for an access token.

    Sends a POST request to the Mercado Livre token endpoint with the
    provided authorization ``code``.

    Args:
        code: The authorization code extracted from the redirect URL.

    Returns:
        dict: The full token response payload, which includes:
              ``access_token``, ``token_type``, ``expires_in``,
              ``scope``, ``user_id``, and ``refresh_token``.

    Raises:
        ValueError: If APP_ID or SECRET_KEY are not configured.
        requests.HTTPError: If the token exchange request fails.
    """
    if not APP_ID or not SECRET_KEY:
        raise ValueError(
            "APP_ID and SECRET_KEY must be set in your .env file."
        )

    payload = {
        "grant_type": "authorization_code",
        "client_id": APP_ID,
        "client_secret": SECRET_KEY,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    response = requests.post(TOKEN_URL, data=payload, timeout=15)
    response.raise_for_status()
    return response.json()


def get_app_token() -> str:
    """Obtain a short-lived app-level access token via the client credentials grant.

    This token is sufficient for read-only API calls (categories, trends,
    highlights) and lets the dashboard work before the user completes OAuth.

    Returns:
        str: A valid ``access_token`` string (expires in ~6 hours).

    Raises:
        ValueError: If ``APP_ID`` or ``SECRET_KEY`` are not configured.
        requests.HTTPError: If the token request fails.
    """
    if not APP_ID or not SECRET_KEY:
        raise ValueError(
            "APP_ID and SECRET_KEY must be set in your .env file."
        )
    payload = {
        "grant_type": "client_credentials",
        "client_id": APP_ID,
        "client_secret": SECRET_KEY,
    }
    response = requests.post(TOKEN_URL, data=payload, timeout=15)
    response.raise_for_status()
    return response.json()["access_token"]


def _auth_headers(access_token: Optional[str] = None) -> dict[str, str]:
    """Build an ``Authorization`` header dict.

    Prefers the caller-supplied *access_token* (user OAuth token). When none
    is provided it falls back to a freshly minted app-level token so all API
    calls succeed even before the user has gone through the OAuth flow.

    Args:
        access_token: Optional user-level OAuth access token.

    Returns:
        dict[str, str]: ``{"Authorization": "Bearer <token>"}`` or ``{}``
        when credentials are not configured (degrades gracefully).
    """
    token = access_token
    if not token:
        try:
            token = get_app_token()
        except Exception:  # noqa: BLE001
            return {}  # let the caller surface the 403 if it occurs
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# API data fetching
# ---------------------------------------------------------------------------


def fetch_categories() -> list[dict]:
    """Fetch all top-level product categories for Brazil (MLB).

    Uses the public endpoint that does **not** require authentication.

    Returns:
        list[dict]: A list of category objects, each containing at least
                    ``id`` and ``name`` keys.

    Raises:
        requests.HTTPError: If the request fails.
        requests.ConnectionError: On network issues.
        requests.Timeout: If the request times out.
    """
    url = f"{API_BASE_URL}/sites/MLB/categories"
    response = requests.get(url, headers=_auth_headers(), timeout=15)
    response.raise_for_status()
    return response.json()


def fetch_trends(
    category_id: str,
    access_token: Optional[str] = None,
) -> list[dict]:
    """Fetch trending searches for a specific MLB category.

    Args:
        category_id: The Mercado Livre category identifier (e.g. ``"MLB1051"``).
        access_token: Optional OAuth access token. When provided it is sent
                      as a ``Bearer`` token in the ``Authorization`` header.

    Returns:
        list[dict]: A list of trend objects. Each object contains at least
                    a ``keyword`` field; some may include additional data such
                    as ``url``.

    Raises:
        requests.HTTPError: If the request fails.
        requests.ConnectionError: On network issues.
        requests.Timeout: If the request times out.
    """
    url = f"{API_BASE_URL}/trends/MLB/{category_id}"
    response = requests.get(url, headers=_auth_headers(access_token), timeout=15)
    response.raise_for_status()
    return response.json()


def fetch_category_highlights(
    category_id: str,
    access_token: Optional[str] = None,
    limit: int = 4,
) -> list[dict]:
    """Fetch the top best-seller products for a given MLB category.

    Uses a three-step approach that works with an app-level token:

    1. **Highlights endpoint** — returns ordered best-seller catalog product
       IDs for the category (``GET /highlights/MLB/category/{category_id}``).
    2. **Product detail** — fetches name and high-res images for each ID
       (``GET /products/{id}``).
    3. **Product items** — fetches the cheapest live listing price
       (``GET /products/{id}/items?limit=1``).

    The product permalink is constructed from the canonical ML product-page
    URL pattern: ``https://www.mercadolivre.com.br/p/{id}``.

    Each returned dict is normalised to:

    .. code-block:: python

        {
            "title":     str,    # product name
            "price":     float,  # lowest BRL price in the active listings
            "permalink": str,    # canonical ML product page URL
            "thumbnail": str,    # HTTPS high-res image URL
        }

    Args:
        category_id:  MLB category identifier (e.g. ``"MLB1051"``).
        access_token: Optional user-level OAuth token; falls back to an
                      app-level token when not provided.
        limit:        Maximum number of products to return (1–20).

    Returns:
        list[dict]: Up to *limit* product dicts. Empty list when no
                    highlights are available for the category.

    Raises:
        requests.HTTPError:       On non-2xx API responses.
        requests.ConnectionError: On network issues.
        requests.Timeout:         If any request times out.
    """
    headers = _auth_headers(access_token)

    # ── Step 1: fetch ordered highlight product IDs ───────────────────────────
    highlights_url = f"{API_BASE_URL}/highlights/MLB/category/{category_id}"
    h_resp = requests.get(highlights_url, headers=headers, timeout=15)

    if h_resp.status_code == 404:
        return []  # category has no highlight data (e.g. real estate, services)
    h_resp.raise_for_status()

    content: list[dict] = h_resp.json().get("content", [])
    if not content:
        return []

    prod_ids: list[str] = [
        item["id"] for item in content[:limit] if item.get("id")
    ]
    if not prod_ids:
        return []

    # ── Steps 2 + 3: product detail + cheapest item price ────────────────────
    products: list[dict] = []
    for prod_id in prod_ids:
        try:
            # Step 2 — product name & images
            p_resp = requests.get(
                f"{API_BASE_URL}/products/{prod_id}",
                headers=headers,
                timeout=15,
            )
            if not p_resp.ok:
                continue
            p_data: dict = p_resp.json()

            name: str = p_data.get("name", "")
            pictures: list[dict] = p_data.get("pictures") or []
            image_url: str = (
                pictures[0].get("url", "") if pictures else ""
            )
            # Normalise to HTTPS
            if image_url.startswith("http://"):
                image_url = "https://" + image_url[len("http://"):]

            # Permalink: standard ML catalog product page
            permalink: str = f"https://www.mercadolivre.com.br/p/{prod_id}"

            # Step 3 — cheapest active item price
            i_resp = requests.get(
                f"{API_BASE_URL}/products/{prod_id}/items",
                params={"limit": 1},
                headers=headers,
                timeout=15,
            )
            price: float = 0.0
            if i_resp.ok:
                items: list[dict] = i_resp.json().get("results", [])
                if items:
                    price = float(items[0].get("price") or 0)

            products.append(
                {
                    "title": name,
                    "price": price,
                    "permalink": permalink,
                    "thumbnail": image_url,
                }
            )
        except Exception:  # noqa: BLE001
            # Skip any individual product that fails; surface the rest
            continue

    return products

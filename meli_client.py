"""
meli_client.py
--------------
Handles all interactions with the Mercado Livre API:
  - OAuth 2.0 authorization URL generation
  - Authorization code exchange for access_token
  - Category listing
  - Trend fetching per category
  - Product search / enrichment per keyword
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

    This token is sufficient for read-only public API calls (categories, trends,
    product search) and removes the need for the user to complete the OAuth flow
    before they can browse the dashboard.

    Returns:
        str: The ``access_token`` string.

    Raises:
        ValueError: If APP_ID or SECRET_KEY are not configured.
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
    """Build an Authorization header dict.

    Prefers the caller-supplied *access_token* (i.e. a user OAuth token).
    When none is given, falls back to a freshly-minted app-level token so all
    API calls work even before the user has authorised the app.

    Args:
        access_token: Optional user-level OAuth access token.

    Returns:
        dict[str, str]: ``{"Authorization": "Bearer <token>"}`` or ``{}`` if
                        credentials are not configured.
    """
    token = access_token
    if not token:
        try:
            token = get_app_token()
        except Exception:  # noqa: BLE001
            # Degrade gracefully — the caller will handle the 403 if it occurs.
            return {}
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# API data fetching
# ---------------------------------------------------------------------------


def fetch_categories() -> list[dict]:
    """Fetch all top-level product categories for Brazil (MLB).

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


def search_top_products_for_keyword(
    keyword: str,
    limit: int = 3,
    access_token: Optional[str] = None,
) -> list[dict]:
    """Search Mercado Livre for the top products matching *keyword*.

    Uses the public ``/sites/MLB/search`` endpoint sorted by relevance so the
    results reflect actual popularity rather than price order.

    Each returned dict is a normalised subset of the full item payload:

    .. code-block:: python

        {
            "title":     str,   # product name
            "price":     float, # price in BRL
            "permalink": str,   # canonical product URL
            "thumbnail": str,   # HTTPS image URL (replace http→https)
        }

    Args:
        keyword:      The search term (e.g. a trending keyword).
        limit:        Maximum number of products to return (1-50).
        access_token: Optional OAuth token sent as a Bearer header.

    Returns:
        list[dict]: Up to *limit* product dicts. Empty list when the query
                    returns no results.

    Raises:
        requests.HTTPError:      On non-2xx API responses.
        requests.ConnectionError: On network issues.
        requests.Timeout:        If the request times out.
    """
    url = f"{API_BASE_URL}/sites/MLB/search"
    params: dict[str, str | int] = {
        "q": keyword,
        "sort": "relevance",
        "limit": min(limit, 50),  # API hard-cap is 50
    }

    response = requests.get(
        url, params=params, headers=_auth_headers(access_token), timeout=15
    )
    response.raise_for_status()

    items: list[dict] = response.json().get("results", [])

    products: list[dict] = []
    for item in items[:limit]:
        # Ensure thumbnail is served over HTTPS (ML sometimes returns http://)
        thumbnail: str = item.get("thumbnail", "")
        if thumbnail.startswith("http://"):
            thumbnail = "https://" + thumbnail[len("http://"):]

        products.append(
            {
                "title": item.get("title", ""),
                "price": item.get("price", 0.0),
                "permalink": item.get("permalink", ""),
                "thumbnail": thumbnail,
            }
        )

    return products

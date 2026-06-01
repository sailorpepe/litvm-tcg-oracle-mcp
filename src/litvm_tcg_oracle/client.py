"""
REST API Client — Thin wrapper for the TCG Price Oracle API.

All off-chain data (search, prices, market snapshots) flows through
oracle.the-undesirables.com, which is backed by a 1.2 GB SQLite
database on the Mac Mini with FTS5 full-text search and 12.7M+ price rows.

Verified Endpoints (as deployed on Mac Mini):
  GET /health              → {"status":"ok", ...}
  GET /api/v1/search       → FTS5 card search (free)
  GET /api/v1/market       → Market snapshot by game (free)
  GET /api/v1/merkle/proof → Merkle proof for product (free)
  GET /api/v1/price        → Card price + history (free)
"""

import logging
import os
from typing import Any, Optional

import requests

logger = logging.getLogger("litvm-tcg-oracle")

# Allow override via environment variable for local development
DEFAULT_BASE_URL = os.environ.get(
    "LITVM_ORACLE_URL",
    "https://oracle.the-undesirables.com",
)
REQUEST_TIMEOUT = 30  # seconds


class OracleClient:
    """Stateless HTTP client for the TCG Price Oracle REST API.
    
    The base URL can be overridden by setting the LITVM_ORACLE_URL
    environment variable. This is useful for local development or
    when running the oracle server on a different host.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = REQUEST_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session: Optional[requests.Session] = None

    @property
    def session(self) -> requests.Session:
        """Lazy-initialize the requests session."""
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": "litvm-tcg-oracle-mcp/1.0",
                "Accept": "application/json",
            })
        return self._session

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        """Execute a GET request and return parsed JSON."""
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            logger.warning(f"Request timed out: {url}")
            return {"error": f"Request timed out after {self.timeout}s", "endpoint": path}
        except requests.exceptions.ConnectionError:
            logger.warning(f"Connection failed: {url}")
            return {
                "error": (
                    "Cannot reach oracle API at "
                    f"{self.base_url}. Ensure the server is running "
                    "and the Cloudflare tunnel is active."
                ),
                "endpoint": path,
                "hint": "Set LITVM_ORACLE_URL env var to override the base URL.",
            }
        except requests.exceptions.HTTPError:
            status = resp.status_code
            body = resp.text[:300]
            logger.warning(f"HTTP {status} from {path}: {body}")
            return {"error": f"HTTP {status}", "detail": body, "endpoint": path}
        except ValueError:
            # Non-JSON response
            return {"error": "Server returned non-JSON response", "endpoint": path}
        except Exception as e:
            logger.exception(f"Unexpected error calling {path}")
            return {"error": str(e), "endpoint": path}

    # ── Public Methods ─────────────────────────────────────────

    def health(self) -> dict:
        """Check API health and get database statistics."""
        return self._get("/health")

    def search(
        self,
        query: str,
        game: Optional[str] = None,
        limit: int = 10,
    ) -> dict:
        """Search cards using FTS5 full-text search.
        
        Args:
            query: Search term (e.g. "charizard", "black lotus")
            game: Optional game filter (e.g. "Pokemon", "Magic")
            limit: Max results, 1-50
            
        Returns:
            {"status":"ok", "query":"...", "data": {"results": [...]}}
        """
        params: dict[str, Any] = {"query": query, "limit": min(limit, 50)}
        if game:
            params["game"] = game
        return self._get("/api/v1/search", params)

    def get_price(
        self,
        product_id: int,
        days: int = 30,
    ) -> dict:
        """Get price and history for a specific product.
        
        Args:
            product_id: TCGPlayer product ID
            days: Days of history (1-365)
            
        Returns:
            Price data with history array
        """
        return self._get(
            "/api/v1/price",
            {"product_id": product_id, "days": min(days, 365)},
        )

    def market_snapshot(
        self,
        game: str = "Pokemon",
        limit: int = 25,
    ) -> dict:
        """Get market snapshot — top cards by value.
        
        Args:
            game: Game name (default "Pokemon")
            limit: Number of cards to return
            
        Returns:
            {"status":"ok", "game":"...", "data": {...}}
        """
        params: dict[str, Any] = {"game": game, "limit": min(limit, 50)}
        return self._get("/api/v1/market", params)

    def merkle_proof(self, product_id: int) -> dict:
        """Get Merkle proof for on-chain price verification.
        
        Args:
            product_id: TCGPlayer product ID
            
        Returns:
            Proof array, root hash, leaf data, and contract info
        """
        return self._get("/api/v1/merkle/proof", {"product_id": product_id})

    def resolve_card(self, card_name: str) -> Optional[dict]:
        """Search for a card by name and return the best match.
        
        Returns the first result dict or None if no match found.
        """
        result = self.search(card_name, limit=1)
        data = result.get("data", result)
        results = data.get("results", [])
        return results[0] if results else None

#!/usr/bin/env python3
"""
LitVM TCG Oracle — MCP Server
==============================
The first Model Context Protocol server for the LitecoinVM ecosystem.

Provides AI agents with direct access to:
  • 432,000+ trading card prices across 13 games
  • On-chain Merkle proof verification on LiteForge (Chain ID 4441)
  • Calibrated conformal risk forecasts — honest VaR + Safe-Hold/Momentum grades
  • Provably-fair Monte Carlo price simulation (Merton/GBM, opt-in)
  • Live oracle contract status via Caldera RPC

Architecture:
  ┌──────────────┐     HTTPS      ┌──────────────────────┐
  │  AI Agent    │ ──────────────►│  TCG Price Oracle    │
  │  (Claude,    │   MCP/stdio    │  REST API (Mac Mini) │
  │   GPT,       │◄──────────────│  oracle.the-         │
  │   Cursor)    │                │  undesirables.com    │
  └──────┬───────┘                └──────────────────────┘
         │                                   │
         │  RPC (read-only)                  │  Daily pipeline
         ▼                                   ▼
  ┌──────────────┐                ┌──────────────────────┐
  │  LiteForge   │                │  SQLite Database     │
  │  Chain 4441  │                │  433K products       │
  │  Merkle +    │◄───────────────│  12.7M price rows    │
  │  V2 Oracle   │   On-chain TX  │  FTS5 search index   │
  └──────────────┘                └──────────────────────┘

Install:
    pip install litvm-tcg-oracle

Claude Desktop config (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "litvm-tcg-oracle": {
          "command": "litvm-tcg-oracle"
        }
      }
    }

License: BUSL-1.1
Built by The Undesirables LLC — the first oracle on LitecoinVM.
"""

import json
import logging
import os
import sys
from typing import Optional

from fastmcp import FastMCP

from litvm_tcg_oracle import __version__
from litvm_tcg_oracle.client import OracleClient
from litvm_tcg_oracle.chain import get_oracle_status, CHAIN_ID, BLOCK_EXPLORER
from litvm_tcg_oracle.simulate import simulate

# ── Logging (stderr only — never corrupt MCP JSON-RPC on stdout) ──
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("litvm-tcg-oracle")

# ── Initialize MCP Server ────────────────────────────────────

mcp = FastMCP(
    "LitVM TCG Oracle",
    instructions=(
        "TCG Price Oracle for the LitecoinVM ecosystem. "
        "Search 433K+ trading card products (276K actively priced) across 13 games, "
        "get real-time market prices, pull a calibrated conformal risk forecast for "
        "any card — honest VaR with Safe-Hold and Momentum letter grades — verify "
        "prices on-chain via Merkle proofs on LiteForge (Chain ID 4441), and run "
        "provably-fair Monte Carlo simulations (Merton/GBM) calibrated from 13.5M+ "
        "real price observations. "
        "Default forecasting is conformal (distribution-free, deterministic, honest "
        "VaR); Monte Carlo is an opt-in stochastic view. "
        "All actively-priced products are cryptographically committed to a daily "
        "Merkle root on the LitecoinVM blockchain — every price can be independently "
        "verified on-chain without trusting this server. "
        "Note: ~157K products in the catalog have no price history (tokens, promos, "
        "bundles, foreign-market-only items). These are searchable but not in the "
        "Merkle tree. "
        "Built by The Undesirables LLC — the first and only oracle on LitecoinVM."
    ),
    version=__version__,
)

# Shared API client
client = OracleClient()


# ═══════════════════════════════════════════════════════════════
# TOOL 1: search_cards
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def search_cards(
    query: str,
    game: Optional[str] = None,
    limit: int = 10,
) -> str:
    """Search 433K+ trading card products by name using full-text search.

    The catalog contains 433K products total — 276K are actively priced
    with current market data. ~157K are catalog-only entries (tokens,
    promos, bundles) with no price history.
    Disney Lorcana, Flesh & Blood, Dragon Ball Super, Digimon, Star Wars,
    Union Arena, MetaZoo, Cardfight Vanguard, and My Hero Academia.

    Returns product IDs (needed for get_price and get_merkle_proof),
    card names, games, and current market prices.

    Args:
        query: Search term (e.g. "charizard base set", "black lotus", "luffy")
        game: Optional game filter (e.g. "Pokemon", "Magic", "Yu-Gi-Oh")
        limit: Number of results (1-50, default 10)
    """
    limit = max(1, min(limit, 50))
    result = client.search(query, game=game, limit=limit)

    if "error" in result:
        return json.dumps(result, indent=2)

    # Normalize response — the API wraps data differently based on version
    data = result.get("data", result)
    raw_results = data.get("results", [])

    output = {
        "query": query,
        "game_filter": game,
        "total_matches": data.get("total", len(raw_results)),
        "results": [_normalize_card(r) for r in raw_results],
        "source": "TCG Price Oracle — oracle.the-undesirables.com",
        "chain": f"LiteForge (Chain ID {CHAIN_ID})",
        "note": "Use product_id with get_price() or get_merkle_proof() for detailed data.",
    }
    return json.dumps(output, indent=2)


# ═══════════════════════════════════════════════════════════════
# TOOL 2: get_price
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def get_price(
    card_name: Optional[str] = None,
    product_id: Optional[int] = None,
    days: int = 30,
) -> str:
    """Get the latest market price and historical price data for a trading card.

    Provide either a card name (fuzzy search) or a TCGPlayer product ID.
    Returns current market price, low (buy-it-now) price, and daily
    price history for the requested time window.

    The price history is what powers the Monte Carlo simulation — it's
    the same data used to calibrate drift and volatility parameters.

    Args:
        card_name: Card name to search (e.g. "Charizard Base Set Holo")
        product_id: TCGPlayer product ID for exact lookup (e.g. 98580)
        days: Days of price history to include (1-365, default 30)
    """
    if not card_name and not product_id:
        return json.dumps({"error": "Provide either card_name or product_id"})

    days = max(1, min(days, 365))

    # Resolve card name to product_id if needed
    if card_name and not product_id:
        card = client.resolve_card(card_name)
        if not card:
            return json.dumps({"error": f"No card found matching '{card_name}'"})
        product_id = card.get("product_id", card.get("productId"))

    # Try the dedicated price endpoint
    price_result = client.get_price(product_id=product_id, days=days)

    if "error" not in price_result:
        return json.dumps(price_result, indent=2)

    # Fallback: return search data if the price endpoint isn't available yet
    logger.warning(f"/api/v1/price returned error, falling back to search data")
    if card_name:
        card = client.resolve_card(card_name)
    else:
        # Search by product_id
        search_result = client.search(str(product_id), limit=1)
        data = search_result.get("data", search_result)
        results = data.get("results", [])
        card = results[0] if results else None

    if card:
        return json.dumps({
            **_normalize_card(card),
            "days_requested": days,
            "price_history": [],
            "note": (
                "Full price history endpoint is being deployed. "
                "Current snapshot price shown. Use simulate_price() for "
                "forward-looking analysis — it calibrates from stored history."
            ),
        }, indent=2)

    return json.dumps({"error": f"Card not found (product_id={product_id})"})


# ═══════════════════════════════════════════════════════════════
# TOOL 3: get_merkle_proof
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def get_merkle_proof(product_id: int) -> str:
    """Get a Merkle proof for on-chain price verification on LitecoinVM.

    WHY THIS MATTERS FOR AI AGENTS:
    Regular API prices require trusting the server. Merkle proofs let you
    VERIFY the price on-chain without trusting anyone. The proof is a
    cryptographic guarantee that this exact price was committed to the
    LitecoinVM blockchain by the oracle operator.

    The TCG Price Oracle commits 276K actively-priced products to a
    single Merkle root on LiteForge daily. This tool returns the proof
    array that can be submitted to the MerklePriceOracle smart contract
    to trustlessly verify any card's price.

    NOTE: Only actively-priced products (market_price > 0) are included
    in the Merkle tree. Zero-price catalog entries cannot be proven.

    Verification flow:
      1. Call this tool with a product_id → get proof + leaf data
      2. Call MerklePriceOracle.verifyPrice() on LiteForge with the proof
      3. Contract verifies the leaf against the committed root
      4. Returns true only if the price matches exactly

    Leaf encoding (matches Solidity):
      keccak256(bytes.concat(keccak256(abi.encode(
        productId, categoryId, name, marketPrice, lowPrice
      ))))

    Standard: OpenZeppelin MerkleProof (double-hash, sorted pairs)

    Args:
        product_id: TCGPlayer product ID (e.g. 98580 for Shadowless Charizard)
    """
    result = client.merkle_proof(product_id)

    if "error" in result:
        return json.dumps({
            **result,
            "hint": (
                "The Merkle proof endpoint may not be deployed yet. "
                "This requires the /api/v1/merkle/proof route on the oracle server."
            ),
        }, indent=2)

    data = result.get("data", result)

    output = {
        "product_id": product_id,
        "proof": data.get("proof", []),
        "root": data.get("root"),
        "leaf_data": data.get("leaf_data"),
        "total_products": data.get("total_products"),
        "data_date": data.get("data_date"),
        "verification": {
            "chain": f"LiteForge Testnet (Chain ID {CHAIN_ID})",
            "explorer": BLOCK_EXPLORER,
            "contract": "MerklePriceOracle",
            "function": (
                "verifyPrice(uint256 productId, uint16 categoryId, "
                "string name, uint256 marketPrice, uint256 lowPrice, "
                "bytes32[] proof)"
            ),
            "standard": "OpenZeppelin MerkleProof (double-hash, sorted pairs)",
            "encoding": (
                "Leaf = keccak256(bytes.concat(keccak256(abi.encode("
                "productId, categoryId, name, marketPrice, lowPrice))))"
            ),
        },
        "why_this_matters": (
            "This proof lets you verify the price ON-CHAIN without trusting "
            "this API server. Submit the proof to the smart contract on "
            "LiteForge and it will cryptographically confirm the price."
        ),
    }
    return json.dumps(output, indent=2)


# ═══════════════════════════════════════════════════════════════
# TOOL 4: get_oracle_status
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def oracle_status() -> str:
    """Get live status of the TCG Price Oracle on LitecoinVM.

    Reads DIRECTLY from the LiteForge blockchain (Chain ID 4441) via
    the Caldera RPC endpoint — this is NOT cached data, it's a live
    on-chain read at the moment you call it.

    Returns:
      • MerklePriceOracle: current root, total products, freshness, update count
      • TCGPriceOracleV2: total TWAP updates, last update timestamp
      • Network: connection status, chain ID, RPC URL, explorer link
      • Database: card count, price rows, latest data date (from API)

    No arguments required.
    """
    # On-chain data from LitecoinVM
    chain_status = get_oracle_status()

    # Off-chain database stats from the API
    health = client.health()

    output = {**chain_status}
    if "error" not in health:
        output["database"] = {
            "status": health.get("status"),
            "api_url": client.base_url,
        }
        # Include any stats the health endpoint provides
        for key in ["cards", "total_cards", "price_rows", "total_prices", "latest_date"]:
            if key in health:
                output["database"][key] = health[key]

    return json.dumps(output, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════
# TOOL 5: get_forecast (conformal — honest default)  ·  TOOL 6: simulate_price (Monte Carlo, opt-in)
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def get_forecast(card_name: str) -> str:
    """Get the calibrated conformal risk forecast for a trading card.

    This is the recommended, honest default forecast — distribution-free,
    deterministic, and never-under-protective. Unlike a Monte Carlo
    simulation it makes NO distributional assumption: the bands are
    calibrated on real cross-card price history, so the stated risk is
    honest out-of-sample (a "5% VaR" means a ~5% loss happens about 5%
    of the time). Each card also gets two plain-English letter grades.

    Returns the agent-complete forecast:
      • price, as_of, regime (calm / normal / jumpy)
      • point estimate, expected 30-day move (move_pct), prob_up
      • 50% / 90% bands, VaR 95 / 99
      • safe_hold grade (A+..F — downside / capital preservation)
      • momentum grade (A+..F, or "NA" on a recent drift spike)
      • plain_english — a one-line human read

    For an opt-in stochastic Monte Carlo view (Merton/GBM), use
    simulate_price instead.

    Args:
        card_name: Card to forecast (e.g. "Charizard Base Set Holo")
    """
    card = client.resolve_card(card_name)
    if not card:
        return json.dumps({"error": f"No card found matching '{card_name}'"})

    normalized = _normalize_card(card)
    product_id = normalized.get("product_id")
    if not product_id:
        return json.dumps({
            "error": f"No product_id resolved for '{card_name}'",
            "card": normalized,
        })

    return json.dumps(client.forecast(product_id), indent=2)


@mcp.tool()
def simulate_price(
    card_name: str,
    days: int = 30,
    model: str = "merton",
    simulations: int = 10000,
) -> str:
    """Run a Monte Carlo price simulation for a trading card (opt-in).

    For the honest DEFAULT forecast — conformal VaR + Safe-Hold/Momentum
    grades — use get_forecast. This tool is the stochastic Monte Carlo
    alternative (Merton/GBM).

    HOW THE MATH WORKS:
    This is NOT fake data. The simulation calibrates parameters from REAL
    market prices stored in the oracle database (12.7M+ price observations):

      1. Look up the card → get product_id via FTS5 search
      2. Pull up to 365 days of daily price history
      3. Resample to weekly buckets for stable drift estimates
      4. Compute annualized drift (μ) and volatility (σ)
      5. Detect price jumps via 2σ threshold on time-scaled returns
      6. Run 10,000+ vectorized numpy simulation paths
      7. Return percentile forecast bands + risk metrics

    If insufficient price history exists (<5 data points), conservative
    TCG market priors are used (3% drift, 40% vol) and clearly labeled
    as "default_tcg_priors" in the response.

    MODELS:
      • "gbm" — Geometric Brownian Motion: dS = μ·S·dt + σ·S·dW
        Standard log-normal diffusion (foundation of Black-Scholes).

      • "merton" — Merton Jump-Diffusion (default):
        dS = (μ − λk)·S·dt + σ·S·dW + J·S·dN
        Adds Poisson-driven price jumps to capture sudden events
        (buyouts, influencer hype, ban lists, reprints).

    RISK METRICS:
      • VaR 95%: "There is a 5% chance the price drops below $X"
      • CVaR 95% (Expected Shortfall): "If that tail event occurs,
        the average loss lands at $Y"

    Args:
        card_name: Card to simulate (e.g. "Charizard Base Set Holo")
        days: Forecast horizon in days (1-365, default 30)
        model: "gbm" or "merton" (default "merton")
        simulations: Number of Monte Carlo paths (100-50000, default 10000)
    """
    # Resolve the card
    card = client.resolve_card(card_name)
    if not card:
        return json.dumps({"error": f"No card found matching '{card_name}'"})

    normalized = _normalize_card(card)
    current_price = normalized.get("market_price", 0)

    if not current_price or current_price <= 0:
        return json.dumps({
            "error": f"No price data available for '{card_name}'",
            "card": normalized,
        })

    # Try to get price history for calibration
    price_history = None
    product_id = normalized.get("product_id")
    if product_id:
        price_result = client.get_price(product_id=product_id, days=365)
        if "error" not in price_result:
            data = price_result.get("data", price_result)
            price_history = data.get("price_history", data.get("history", []))

    # Clamp inputs
    days = max(1, min(days, 365))
    simulations = max(100, min(simulations, 50000))
    model = model if model in ("gbm", "merton") else "merton"

    sim_result = simulate(
        card_name=normalized.get("name", card_name),
        current_price=current_price,
        days=days,
        model=model,
        simulations=simulations,
        price_history=price_history,
    )

    # Add product context
    sim_result["product_id"] = product_id
    sim_result["game"] = normalized.get("game", "")

    return json.dumps(sim_result, indent=2)


# ═══════════════════════════════════════════════════════════════
# TOOL 6: get_market_snapshot
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def get_market_snapshot(
    game: str = "Pokemon",
    limit: int = 25,
) -> str:
    """Get a market overview — top trading cards sorted by value.

    Returns the highest-value cards for a specific game with current
    market prices and low (buy-it-now) prices.

    Games: Pokemon, Magic, Yu-Gi-Oh, One Piece, Disney Lorcana,
    Flesh and Blood, Dragon Ball Super, Digimon, Star Wars,
    Union Arena, MetaZoo, Cardfight Vanguard, My Hero Academia.

    Args:
        game: Game name (default "Pokemon")
        limit: Number of cards to return (1-50, default 25)
    """
    limit = max(1, min(limit, 50))
    result = client.market_snapshot(game=game, limit=limit)

    if "error" in result:
        return json.dumps(result, indent=2)

    return json.dumps(result, indent=2)


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _normalize_card(card: dict) -> dict:
    """Normalize card data from different API response formats.
    
    The API has evolved over time and field names vary between
    versions (camelCase vs snake_case). This ensures consistent output.
    """
    return {
        "product_id": card.get("product_id", card.get("productId")),
        "name": card.get("name", card.get("cleanName", card.get("clean_name", ""))),
        "game": card.get("game", card.get("categoryName", card.get("category_name", ""))),
        "market_price": card.get("market_price", card.get("marketPrice")),
        "low_price": card.get("low_price", card.get("lowPrice")),
    }


# ═══════════════════════════════════════════════════════════════
# Entrypoint
# ═══════════════════════════════════════════════════════════════

def main():
    """Run the MCP server via stdio transport."""
    logger.info(
        f"LitVM TCG Oracle MCP Server v{__version__} starting "
        f"(API: {client.base_url})"
    )
    mcp.run()


if __name__ == "__main__":
    main()

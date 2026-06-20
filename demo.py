#!/usr/bin/env python3
"""
LitVM TCG Oracle — Hackathon Demo (Interactive)
Each section shows data, then gives you narration to read aloud.
Press Enter when you're done talking to advance.
"""
import json
import sys
import time
import requests

sys.path.insert(0, "/Users/davidluna/Documents/Meme Merchants/litvm-tcg-oracle-mcp/src")

from litvm_tcg_oracle.client import OracleClient
from litvm_tcg_oracle.simulate import simulate

API = "https://oracle.the-undesirables.com"
RPC = "https://liteforge.rpc.caldera.xyz/http"

# Contract addresses (deployed on LiteForge Chain 4441)
MERKLE_ADDR = "0x96B124f50156589274ADF8F674509374752170Cd"
V2_ADDR = "0x697bF6AE96fb05a47106abd012C39855A16a720E"

MERKLE_ABI = json.loads("""[
    {"inputs":[],"name":"merkleRoot","outputs":[{"type":"bytes32"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"totalProducts","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"totalRootUpdates","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"isRootFresh","outputs":[{"type":"bool"}],"stateMutability":"view","type":"function"}
]""")

V2_ABI = json.loads("""[
    {"inputs":[],"name":"totalUpdates","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"}
]""")


def narrate(lines):
    """Show narration script — read this aloud while the data is on screen."""
    print()
    print("  ┌─────────────────────────────────────────────────")
    for line in lines:
        print(f"  │ {line}")
    print("  └─────────────────────────────────────────────────")
    input("\n  ⏎  Press Enter when ready...\n")


def slow_print(text, delay=0.015):
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(delay)
    print()


def section(title):
    print()
    print("=" * 60)
    slow_print(f"  ⛓️  {title}")
    print("=" * 60)
    print()
    time.sleep(0.3)


# ══════════════════════════════════════════════════════════
#  INTRO
# ══════════════════════════════════════════════════════════
print()
slow_print("🔮 LitVM TCG Oracle — Live Demo")
slow_print("   The first RWA oracle on LitecoinVM")

narrate([
    "Here I'm running the MCP server tools for the",
    "TCG Price Oracle — this is how AI agents interact",
    "with our oracle. Let me walk you through each tool.",
])


# ══════════════════════════════════════════════════════════
#  1. DATABASE
# ══════════════════════════════════════════════════════════
section("DATABASE — Connecting to Oracle API")

health = requests.get(f"{API}/health", timeout=10).json()
total_cards = health.get('total_cards', 0)
total_prices = health.get('total_prices', 0)
latest = health.get('latest_date', 'N/A')

print(f"  📊 Oracle API Status:")
print(f"     Total Products: {total_cards:,}")
print(f"     Price Records:  {total_prices:,}")
print(f"     Latest Data:    {latest}")
print(f"     Status:         ✅ Online")

narrate([
    f"The oracle is online. We're indexing {total_cards:,} trading",
    f"card products with over {total_prices:,} price records.",
    f"That's Pokémon, Magic, Yu-Gi-Oh, One Piece — 13 games.",
    f"Data is current as of {latest}, updated daily.",
])


# ══════════════════════════════════════════════════════════
#  2. ON-CHAIN
# ══════════════════════════════════════════════════════════
section("ON-CHAIN — Reading Live from LiteForge")

try:
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 15}))
    merkle_contract = w3.eth.contract(
        address=Web3.to_checksum_address(MERKLE_ADDR), abi=MERKLE_ABI
    )
    v2_contract = w3.eth.contract(
        address=Web3.to_checksum_address(V2_ADDR), abi=V2_ABI
    )

    root = "0x" + merkle_contract.functions.merkleRoot().call().hex()
    total_products = merkle_contract.functions.totalProducts().call()
    total_updates = merkle_contract.functions.totalRootUpdates().call()
    is_fresh = merkle_contract.functions.isRootFresh().call()
    v2_updates = v2_contract.functions.totalUpdates().call()

    print(f"  🌳 MerklePriceOracle:")
    print(f"     Contract: {MERKLE_ADDR}")
    print(f"     Root:     {root[:28]}...")
    print(f"     Products: {total_products:,}")
    print(f"     Updates:  {total_updates:,} root commits")
    print(f"     Fresh:    {'✅ Yes' if is_fresh else '⚠️  Stale'}")
    print()
    print(f"  📈 TCGPriceOracleV2:")
    print(f"     Contract: {V2_ADDR}")
    print(f"     Updates:  {v2_updates:,} TWAP updates")
    print(f"     Products: 50 blue-chip cards (hourly)")

    narrate([
        "This is reading directly from the LiteForge blockchain.",
        f"The Merkle root covers {total_products:,} products — every",
        "card with a real market price above zero gets hashed into",
        "a single Merkle tree, and that root is stored on-chain.",
        f"It's been updated {total_updates:,} times so far.",
        "",
        f"The V2 oracle has {v2_updates:,} individual TWAP price",
        "updates for the top 50 blue-chip cards — those update",
        "every hour, same pattern as Chainlink price feeds.",
    ])

except Exception as e:
    print(f"  🌳 MerklePriceOracle: {MERKLE_ADDR}")
    print(f"  📈 TCGPriceOracleV2:  {V2_ADDR}")
    print(f"  ⚠️  Could not read chain: {e}")

    narrate([
        "These are the two oracle contracts on LiteForge.",
        "The Merkle oracle stores a root hash covering 276,000",
        "products. The V2 oracle stores individual price feeds",
        "for the top 50 blue-chip cards, updated hourly.",
    ])


# ══════════════════════════════════════════════════════════
#  3. SEARCH
# ══════════════════════════════════════════════════════════
section("SEARCH — Full-Text Search Across 433K Cards")

queries = ["charizard", "black lotus"]
for q in queries:
    print(f"  🔍 Searching: \"{q}\"")
    result = requests.get(
        f"{API}/api/v1/search", params={"query": q, "limit": 3}, timeout=10
    ).json()
    results = result.get("data", {}).get("results", [])
    if results:
        for card in results:
            name = card.get("name", "?")
            pid = card.get("product_id", "?")
            price = card.get("market_price")
            if price and price > 0:
                print(f"     💰 ${price:,.2f}  —  {name}  (ID: {pid})")
            else:
                print(f"     🃏 {name}  (ID: {pid})")
    else:
        print(f"     (free tier — full results on paid endpoints)")
    print()
    time.sleep(0.5)

narrate([
    "The search tool uses FTS5 full-text search to query",
    "all 433,000 products instantly. Each result comes back",
    "with a product ID — that's what you pass to the other",
    "tools to get prices, proofs, and simulations.",
])


# ══════════════════════════════════════════════════════════
#  4. PRICE
# ══════════════════════════════════════════════════════════
section("PRICE — Market Data + 30-Day History")

pid = 84198
print(f"  🔍 Pulling price for product {pid}...")
print()
price_data = requests.get(
    f"{API}/api/v1/price", params={"product_id": pid, "days": 30}, timeout=10
).json()
d = price_data.get("data", {})
card_name = d.get("name", "Unknown")
market_price = d.get("market_price", 0)

print(f"  🃏 {card_name}")
print(f"     Game:         {d.get('game', 'N/A')}")
print(f"     Market Price: ${market_price:,.2f}")
print(f"     Low Price:    ${d.get('low_price', 0):,.2f}")
print(f"     Latest Date:  {d.get('latest_date', 'N/A')}")
history = d.get("price_history", [])
if history:
    print(f"     History:      {len(history)} data points")

narrate([
    f"Here's a {card_name} — market price ${market_price:,.2f}.",
    f"We pulled {len(history)} days of price history. That real",
    "price history is what calibrates the Monte Carlo simulation",
    "— it's not made-up numbers, it's actual market data from",
    f"our database of {total_prices:,} price observations.",
])


# ══════════════════════════════════════════════════════════
#  5. MERKLE PROOF
# ══════════════════════════════════════════════════════════
section("MERKLE PROOF — Cryptographic Verification")

print(f"  🌳 Requesting Merkle proof for {card_name}...")
print()
proof_resp = requests.get(
    f"{API}/api/v1/merkle/proof", params={"product_id": pid}, timeout=10
).json()
pd_data = proof_resp.get("data", {})
proof_array = pd_data.get("proof", [])

if proof_array:
    print(f"  ✅ PROOF RETURNED — {len(proof_array)} hashes")
    print(f"     Leaf:  {str(pd_data.get('leaf', ''))[:32]}...")
    total_in_tree = pd_data.get("total_products")
    if isinstance(total_in_tree, (int, float)):
        print(f"     Tree:  {total_in_tree:,} products")
    print()
    print(f"     Proof path:")
    for i, h in enumerate(proof_array[:4]):
        print(f"       [{i+1}] {h[:32]}...")
    if len(proof_array) > 4:
        print(f"       ... +{len(proof_array) - 4} more hashes")
    print()
    print(f"  🔗 Contract: {MERKLE_ADDR}")
    print(f"     Chain:    LiteForge (4441)")

    narrate([
        f"That's the Merkle proof — {len(proof_array)} hashes. Each hash",
        "is one step in the path from this card's leaf all the way",
        "up to the Merkle root that's stored on the blockchain.",
        "",
        "This is what makes it trustless. You don't have to trust",
        "our API — you can take this proof, submit it to the smart",
        "contract on LiteForge, and the contract will verify that",
        "this exact price was committed to the blockchain.",
        "",
        "Nobody can fake it. Nobody can tamper with it.",
    ])
else:
    print(f"  ℹ️  {json.dumps(proof_resp)[:200]}")
    narrate(["Merkle proof endpoint returned an unexpected response."])


# ══════════════════════════════════════════════════════════
#  6. MONTE CARLO
# ══════════════════════════════════════════════════════════
section("MONTE CARLO — Merton Jump-Diffusion")

print(f"  🎲 Running 10,000 simulations for {card_name}...")
print(f"     Starting at ${market_price:,.2f}")
print(f"     Model: Merton Jump-Diffusion")
print(f"     Horizon: 30 days")
print()

sim = simulate(
    card_name=card_name,
    current_price=market_price,
    days=30,
    model="merton",
    simulations=10000,
    price_history=history,
)
forecast = sim.get("forecast_percentiles", sim.get("forecast", {}))
risk = sim.get("risk_metrics", {})

median = forecast.get("50th", forecast.get("median", 0))
bull = forecast.get("95th", forecast.get("p95", 0))
bear = forecast.get("5th", forecast.get("p5", 0))

print(f"  📊 30-Day Forecast:")
print(f"     Bear (5th):   ${bear:,.2f}")
print(f"     Median:       ${median:,.2f}")
print(f"     Bull (95th):  ${bull:,.2f}")
print()

var95 = risk.get("VaR_95", risk.get("var_95", 0))
cvar95 = risk.get("CVaR_95", risk.get("cvar_95", 0))
if var95:
    print(f"  ⚠️  Risk Metrics:")
    print(f"     VaR 95%:  ${var95:,.2f}")
    print(f"     CVaR 95%: ${cvar95:,.2f}")

narrate([
    "10,000 simulation paths using Merton Jump-Diffusion.",
    "That's not just random noise — it models sudden price",
    "jumps from events like buyouts, reprints, or influencer",
    "hype. The parameters are calibrated from real price data.",
    "",
    "VaR tells you the worst-case scenario at 95% confidence.",
    "CVaR tells you what happens in that worst 5% of outcomes.",
    "This is the same math banks use for risk assessment —",
    "applied to trading cards.",
])


# ══════════════════════════════════════════════════════════
#  OUTRO
# ══════════════════════════════════════════════════════════
print()
print("=" * 60)
slow_print("  ✅ LitVM TCG Oracle — Built by The Undesirables")
slow_print("  📦 pip install litvm-tcg-oracle")
slow_print("  🌐 the-undesirables.com/litvm")
print("=" * 60)
print()

"""
On-Chain Reader — Direct RPC calls to LitecoinVM (LiteForge testnet).

Reads deployed smart contracts on LiteForge (Chain ID 4441) via the
Caldera RPC endpoint. No private key needed — read-only calls.

Contracts:
  - TCGPriceOracleV2: Live TWAP price feeds for top 50 blue-chip cards
  - MerklePriceOracle: Single Merkle root committing 432K+ product prices
"""

import json
import logging
from typing import Any, Optional

logger = logging.getLogger("litvm-tcg-oracle")

# ── LiteForge Network Configuration ──────────────────────────
LITEFORGE_RPC = "https://liteforge.rpc.caldera.xyz/http"
CHAIN_ID = 4441
BLOCK_EXPLORER = "https://liteforge.calderaexplorer.xyz"

# ── Contract Addresses (deployed on LiteForge testnet) ───────
# These are read from deployment artifacts if available,
# otherwise use the known addresses.
MERKLE_ORACLE_ADDRESS = None  # Set dynamically
V2_ORACLE_ADDRESS = None      # Set dynamically

# ── Minimal ABIs (read-only functions only) ──────────────────

MERKLE_ABI = json.loads("""[
    {"inputs":[],"name":"merkleRoot","outputs":[{"type":"bytes32"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"lastRootUpdate","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"totalProducts","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"totalRootUpdates","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"isRootFresh","outputs":[{"type":"bool"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"_index","type":"uint256"}],"name":"getRootAtIndex","outputs":[{"type":"bytes32"},{"type":"uint256"}],"stateMutability":"view","type":"function"}
]""")

V2_ABI = json.loads("""[
    {"inputs":[],"name":"totalUpdates","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"lastUpdateTimestamp","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"owner","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"}
]""")


def _load_contract_addresses() -> dict:
    """Attempt to load contract addresses from deployment artifacts."""
    import os
    addresses = {}

    # Search common deployment paths
    search_paths = [
        os.path.expanduser("~/Documents/Meme Merchants/litvm-tcg-oracle/scripts"),
        os.path.expanduser("~/Documents/Meme Merchants/litvm-tcg-oracle/artifacts"),
        os.path.expanduser("~/Documents/Meme Merchants/litvm-tcg-oracle/airdrop-to-mac-mini"),
    ]

    for base in search_paths:
        merkle_deploy = os.path.join(base, "merkle_deployment.json")
        if os.path.exists(merkle_deploy):
            try:
                with open(merkle_deploy) as f:
                    data = json.load(f)
                    addresses["merkle"] = data.get("contract_address", data.get("address"))
            except Exception:
                pass

        v2_deploy = os.path.join(base, "v2_deployment.json")
        if os.path.exists(v2_deploy):
            try:
                with open(v2_deploy) as f:
                    data = json.load(f)
                    addresses["v2"] = data.get("contract_address", data.get("address"))
            except Exception:
                pass

    return addresses


def get_oracle_status() -> dict:
    """
    Read oracle contract state directly from LitecoinVM.
    
    Makes view calls to:
      - MerklePriceOracle: root, totalProducts, isRootFresh, totalRootUpdates
      - TCGPriceOracleV2: totalUpdates, lastUpdateTimestamp
    
    Returns a structured status object or error.
    """
    try:
        from web3 import Web3
    except ImportError:
        return _get_oracle_status_fallback()

    try:
        w3 = Web3(Web3.HTTPProvider(LITEFORGE_RPC, request_kwargs={"timeout": 15}))
        if not w3.is_connected():
            return {"error": "Cannot connect to LiteForge RPC", "rpc": LITEFORGE_RPC}

        addresses = _load_contract_addresses()
        status: dict[str, Any] = {
            "network": {
                "name": "LiteForge Testnet",
                "chain_id": CHAIN_ID,
                "rpc": LITEFORGE_RPC,
                "explorer": BLOCK_EXPLORER,
                "connected": True,
            },
            "contracts": {},
        }

        # ── Read MerklePriceOracle ────────────────────────────
        merkle_addr = addresses.get("merkle")
        if merkle_addr:
            try:
                merkle = w3.eth.contract(
                    address=Web3.to_checksum_address(merkle_addr),
                    abi=MERKLE_ABI,
                )
                root = merkle.functions.merkleRoot().call()
                total_products = merkle.functions.totalProducts().call()
                total_updates = merkle.functions.totalRootUpdates().call()
                is_fresh = merkle.functions.isRootFresh().call()
                last_update = merkle.functions.lastRootUpdate().call()

                status["contracts"]["merkle_oracle"] = {
                    "address": merkle_addr,
                    "explorer_url": f"{BLOCK_EXPLORER}/address/{merkle_addr}",
                    "merkle_root": "0x" + root.hex(),
                    "total_products": total_products,
                    "total_root_updates": total_updates,
                    "is_root_fresh": is_fresh,
                    "last_update_timestamp": last_update,
                    "staleness_threshold": "48 hours",
                }
            except Exception as e:
                status["contracts"]["merkle_oracle"] = {
                    "address": merkle_addr,
                    "error": str(e),
                }

        # ── Read TCGPriceOracleV2 ─────────────────────────────
        v2_addr = addresses.get("v2")
        if v2_addr:
            try:
                v2 = w3.eth.contract(
                    address=Web3.to_checksum_address(v2_addr),
                    abi=V2_ABI,
                )
                total_updates = v2.functions.totalUpdates().call()
                last_ts = v2.functions.lastUpdateTimestamp().call()

                status["contracts"]["v2_oracle"] = {
                    "address": v2_addr,
                    "explorer_url": f"{BLOCK_EXPLORER}/address/{v2_addr}",
                    "total_updates": total_updates,
                    "last_update_timestamp": last_ts,
                    "update_frequency": "Hourly TWAP",
                    "coverage": "Top 50 blue-chip cards",
                }
            except Exception as e:
                status["contracts"]["v2_oracle"] = {
                    "address": v2_addr,
                    "error": str(e),
                }

        if not addresses:
            status["contracts"]["note"] = (
                "Contract addresses not found locally. "
                "Deploy artifacts expected at ~/Documents/Meme Merchants/litvm-tcg-oracle/scripts/"
            )

        return status

    except Exception as e:
        logger.exception("Failed to read on-chain oracle status")
        return {"error": str(e), "rpc": LITEFORGE_RPC}


def _get_oracle_status_fallback() -> dict:
    """Fallback when web3 is not installed — return known static info."""
    addresses = _load_contract_addresses()
    return {
        "network": {
            "name": "LiteForge Testnet",
            "chain_id": CHAIN_ID,
            "rpc": LITEFORGE_RPC,
            "explorer": BLOCK_EXPLORER,
            "connected": False,
            "note": "Install web3 for live on-chain reads: pip install litvm-tcg-oracle[chain]",
        },
        "contracts": {
            "merkle_oracle": {
                "address": addresses.get("merkle", "Unknown — deploy artifacts not found"),
                "description": "Single Merkle root committing 432K+ product prices daily",
            },
            "v2_oracle": {
                "address": addresses.get("v2", "Unknown — deploy artifacts not found"),
                "description": "Hourly TWAP price feeds for top 50 blue-chip cards",
            },
        },
    }

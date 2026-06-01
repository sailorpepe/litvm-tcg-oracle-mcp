import json
import pytest
from unittest.mock import patch, MagicMock
from litvm_tcg_oracle.client import OracleClient
from litvm_tcg_oracle.simulate import simulate, _calibrate_from_history


class TestOracleClient:
    """Tests for the REST API client."""

    def test_client_default_url(self):
        c = OracleClient()
        assert "oracle.the-undesirables.com" in c.base_url

    def test_client_custom_url(self):
        c = OracleClient(base_url="http://localhost:8402")
        assert c.base_url == "http://localhost:8402"

    def test_client_strips_trailing_slash(self):
        c = OracleClient(base_url="http://localhost:8402/")
        assert c.base_url == "http://localhost:8402"

    def test_session_lazy_init(self):
        c = OracleClient()
        assert c._session is None
        _ = c.session
        assert c._session is not None

    def test_connection_error_handled(self):
        c = OracleClient(base_url="http://localhost:99999", timeout=1)
        result = c.health()
        assert "error" in result


class TestSimulation:
    """Tests for Monte Carlo simulation engine."""

    def test_gbm_basic(self):
        result = simulate(
            card_name="Test Card",
            current_price=100.0,
            days=30,
            model="gbm",
            simulations=1000,
        )
        assert result["model"] == "geometric_brownian_motion"
        assert result["current_price"] == 100.0
        assert result["simulations"] == 1000
        assert "forecast_percentiles" in result
        assert "risk_metrics" in result
        assert result["param_source"] == "default_tcg_priors"

    def test_merton_basic(self):
        result = simulate(
            card_name="Test Card",
            current_price=50.0,
            days=60,
            model="merton",
            simulations=500,
        )
        assert result["model"] == "merton_jump_diffusion"
        assert "jump_intensity_lambda" in result["model_params"]
        assert "model_equation" in result["model_params"]

    def test_percentiles_ordered(self):
        result = simulate(
            card_name="Test",
            current_price=200.0,
            days=30,
            model="gbm",
            simulations=5000,
        )
        p = result["forecast_percentiles"]
        assert p["5th"] <= p["25th"] <= p["50th"] <= p["75th"] <= p["95th"]

    def test_risk_metrics_present(self):
        result = simulate(
            card_name="Test",
            current_price=100.0,
            days=30,
            model="merton",
            simulations=1000,
        )
        rm = result["risk_metrics"]
        assert "VaR_95" in rm
        assert "CVaR_95" in rm
        assert rm["VaR_95"] <= result["current_price"]  # VaR should be below current

    def test_calibration_with_history(self):
        # Fake 60 days of price data with upward drift
        history = []
        price = 100.0
        import random
        random.seed(42)
        for i in range(60):
            price *= 1.0 + random.gauss(0.001, 0.02)
            history.append({
                "date": f"2026-{(i // 30) + 1:02d}-{(i % 28) + 1:02d}",
                "market_price": round(price, 2),
            })

        result = simulate(
            card_name="Calibrated Card",
            current_price=price,
            days=30,
            model="merton",
            simulations=2000,
            price_history=history,
        )
        assert result["param_source"] == "calibrated_from_market_data"
        assert "calibration_metadata" in result

    def test_calibration_insufficient_data(self):
        history = [
            {"date": "2026-01-01", "market_price": 10.0},
            {"date": "2026-01-02", "market_price": 11.0},
        ]
        result = _calibrate_from_history(history)
        assert result is None  # Not enough data points

    def test_simulation_clamps(self):
        result = simulate(
            card_name="Test",
            current_price=100.0,
            days=999,  # Should clamp to 365
            model="invalid",  # Should default
            simulations=999999,  # Should clamp to 50000
        )
        # Should still return valid result with defaults
        assert result["simulations"] <= 50000


class TestChain:
    """Tests for on-chain reader."""

    def test_fallback_without_web3(self):
        from litvm_tcg_oracle.chain import _get_oracle_status_fallback
        result = _get_oracle_status_fallback()
        assert result["network"]["chain_id"] == 4441
        assert result["network"]["name"] == "LiteForge Testnet"
        assert "contracts" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

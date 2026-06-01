"""
Monte Carlo Simulation Engine — Stochastic price forecasting with real market data.

This module implements two industry-standard models for price simulation:

  1. Geometric Brownian Motion (GBM)
     The foundation of Black-Scholes option pricing. Models price as a
     log-normal random walk with constant drift (μ) and volatility (σ).
     
       dS = μ·S·dt + σ·S·dW
     
     Where dW is a Wiener process (standard Brownian motion).

  2. Merton Jump-Diffusion (MJD)
     Extends GBM by adding Poisson-distributed price jumps to capture
     sudden market events (buyouts, influencer videos, ban lists, reprints).
     
       dS = (μ − λ·k)·S·dt + σ·S·dW + J·S·dN
     
     Where:
       - dN ~ Poisson(λ·dt) is a jump arrival process
       - J ~ LogNormal(μ_j, σ_j) is the jump magnitude
       - k = E[e^J] - 1 is the drift compensator
       - λ is the jump intensity (jumps per year)

Calibration Pipeline:
  1. Look up card by name → get product_id via FTS5 search
  2. Pull chronological price_history from the oracle database
  3. Resample to weekly buckets for stable drift estimates (Fix #1)
  4. Compute annualized μ (drift) and σ (volatility)
  5. Detect jumps via 2σ threshold on time-scaled returns (Fix #3)
  6. Compute standard errors for parameter confidence (Fix #4)
  7. Detect mean-reversion via lag-1 autocorrelation (Fix #5)
  8. Run vectorized numpy simulation with calibrated parameters
  9. Return percentile bands + VaR/CVaR risk metrics

All parameters are calibrated from REAL price data — 12.7M+ rows across
433K products, collected daily from TCGPlayer market prices. When fewer
than 5 data points exist, falls back to conservative TCG market priors
(3% drift, 40% vol) clearly labeled as "default_tcg_priors".

References:
  - Merton, R.C. (1976). "Option pricing when underlying stock returns
    are discontinuous." Journal of Financial Economics, 3(1-2), 125-144.
  - Black, F. & Scholes, M. (1973). "The Pricing of Options and Corporate
    Liabilities." Journal of Political Economy, 81(3), 637-654.
"""

import logging
import math
import statistics
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("litvm-tcg-oracle")


def simulate(
    card_name: str,
    current_price: float,
    days: int = 30,
    model: str = "merton",
    simulations: int = 10000,
    price_history: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """
    Run Monte Carlo price simulation using calibrated market parameters.

    Args:
        card_name: Name of the card being simulated.
        current_price: Current market price in USD.
        days: Forecast horizon (1-365 days).
        model: "gbm" for Geometric Brownian Motion, "merton" for Jump-Diffusion.
        simulations: Number of Monte Carlo paths (100-50,000).
        price_history: Optional pre-fetched price history as list of
                       {"date": "YYYY-MM-DD", "market_price": float} dicts.
                       If not provided, uses conservative default parameters.

    Returns:
        Dictionary with forecast percentiles, risk metrics, model parameters,
        and calibration metadata.
    """
    try:
        import numpy as np
    except ImportError:
        return {"error": "numpy is required for simulation. Install: pip install numpy"}

    # Calibrate parameters from price history (or use defaults)
    calibrated = _calibrate_from_history(price_history) if price_history else None

    if calibrated:
        mu = calibrated["mu_annual"]
        sigma = calibrated["sigma_annual"]
        lambda_jump = calibrated.get("jump_intensity_lambda", 2.0)
        mu_j = calibrated.get("jump_mean_mu_j", -0.05)
        sigma_j = calibrated.get("jump_vol_sigma_j", 0.10)
        param_source = "calibrated_from_market_data"
    else:
        # Conservative TCG market priors — clearly labeled
        mu = 0.03        # 3% annual drift (collectibles tend to appreciate slowly)
        sigma = 0.40     # 40% annual vol (typical for mid-liquidity TCG products)
        lambda_jump = 2.0  # ~2 jumps per year
        mu_j = -0.05     # Jumps average -5% (asymmetric downside risk)
        sigma_j = 0.10   # Jump size std dev 10%
        param_source = "default_tcg_priors"

    dt = 1.0 / 365.0
    n_sims = min(simulations, 50000)

    if model == "merton":
        # ── Merton Jump-Diffusion (vectorized) ──────────────────
        Z = np.random.standard_normal((n_sims, days))
        N = np.random.poisson(lambda_jump * dt, (n_sims, days))
        J = N * np.random.normal(mu_j, sigma_j, (n_sims, days))
        J_cumulative = np.cumsum(J, axis=1)

        t = np.arange(1, days + 1) * dt
        # Drift compensator: E[e^J] = e^(μ_j + 0.5·σ_j²)
        jump_compensator = lambda_jump * (np.exp(mu_j + 0.5 * sigma_j**2) - 1)
        drift = (mu - 0.5 * sigma**2 - jump_compensator) * t
        diffusion = sigma * np.cumsum(np.sqrt(dt) * Z, axis=1)

        paths = current_price * np.exp(drift + diffusion + J_cumulative)
        final_prices = paths[:, -1]
        model_label = "merton_jump_diffusion"
        model_params = {
            "drift_mu": round(mu, 4),
            "diffusion_sigma": round(sigma, 4),
            "jump_intensity_lambda": round(lambda_jump, 4),
            "jump_mean_mu_j": round(mu_j, 4),
            "jump_vol_sigma_j": round(sigma_j, 4),
            "model_equation": "dS = (μ − λk)·S·dt + σ·S·dW + J·S·dN",
        }
    else:
        # ── Geometric Brownian Motion (vectorized) ──────────────
        Z = np.random.standard_normal((n_sims, days))
        t = np.arange(1, days + 1) * dt
        drift = (mu - 0.5 * sigma**2) * t
        diffusion = sigma * np.cumsum(np.sqrt(dt) * Z, axis=1)

        paths = current_price * np.exp(drift + diffusion)
        final_prices = paths[:, -1]
        model_label = "geometric_brownian_motion"
        model_params = {
            "drift_mu": round(mu, 4),
            "diffusion_sigma": round(sigma, 4),
            "model_equation": "dS = μ·S·dt + σ·S·dW",
        }

    # ── Risk Metrics ────────────────────────────────────────────
    sorted_prices = np.sort(final_prices)
    n = len(sorted_prices)
    var_95_price = float(sorted_prices[int(n * 0.05)])
    tail = sorted_prices[:int(n * 0.05)]
    cvar_95_price = float(np.mean(tail)) if len(tail) > 0 else var_95_price

    var_95_return = round(((var_95_price - current_price) / current_price) * 100, 2)
    cvar_95_return = round(((cvar_95_price - current_price) / current_price) * 100, 2)

    result: dict[str, Any] = {
        "card_name": card_name,
        "current_price": current_price,
        "model": model_label,
        "days": days,
        "simulations": n_sims,
        "param_source": param_source,
        "model_params": model_params,
        "forecast_percentiles": {
            "5th": round(float(sorted_prices[int(n * 0.05)]), 4),
            "25th": round(float(sorted_prices[int(n * 0.25)]), 4),
            "50th": round(float(sorted_prices[int(n * 0.50)]), 4),
            "75th": round(float(sorted_prices[int(n * 0.75)]), 4),
            "95th": round(float(sorted_prices[int(n * 0.95)]), 4),
        },
        "risk_metrics": {
            "VaR_95": round(var_95_price, 4),
            "VaR_95_pct": var_95_return,
            "CVaR_95": round(cvar_95_price, 4),
            "CVaR_95_pct": cvar_95_return,
            "interpretation": (
                f"95% VaR: There is a 5% chance the price drops below "
                f"${round(var_95_price, 2)} ({var_95_return}%) over {days} days. "
                f"Expected Shortfall (CVaR): If that tail event occurs, the average "
                f"loss lands at ${round(cvar_95_price, 2)} ({cvar_95_return}%)."
            ),
        },
    }

    if calibrated:
        result["calibration_metadata"] = {
            "method": calibrated.get("param_source_detail"),
            "data_points": calibrated.get("data_points"),
            "observation_span_days": calibrated.get("observation_span_days"),
            "jumps_detected": calibrated.get("jumps_detected"),
            "param_confidence": calibrated.get("param_confidence"),
            "mean_reversion": calibrated.get("mean_reversion"),
        }

    return result


def _calibrate_from_history(price_history: list[dict]) -> Optional[dict[str, Any]]:
    """
    Calibrate drift (μ), volatility (σ), and jump parameters from
    real price history data.

    Methodology:
      1. Weekly resampling — groups daily prices into ISO-week buckets
         and computes log-returns between weekly closing prices. This
         eliminates the /√Δt scaling problem that plagues irregular
         daily data and produces stable annual drift estimates.
         
      2. Time-scaled daily fallback — when fewer than 8 weeks of data
         exist, computes log-returns scaled by √Δt to normalize for
         irregular observation gaps.
         
      3. Jump detection — identifies returns exceeding 2σ on the
         time-scaled series. Jump intensity (λ) is annualized as
         count/years. Jump mean and volatility are computed from the
         jump subset.
         
      4. Standard errors — SE(μ) = σ/√n, SE(σ) = σ/√(2(n-1)),
         SE(λ) = √(λ/T). These quantify parameter uncertainty.
         
      5. Mean-reversion — lag-1 autocorrelation of returns. Negative
         values indicate mean-reverting behavior (common in TCG
         markets with seasonal reprints and hype cycles).
    """
    if not price_history or len(price_history) < 5:
        return None

    try:
        # Parse dates and prices
        dated_prices = []
        for entry in price_history:
            try:
                date_str = entry.get("date", "")
                price = float(entry.get("market_price", entry.get("marketPrice", 0)))
                if price > 0 and date_str:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    dated_prices.append((dt, price))
            except (ValueError, TypeError):
                continue

        if len(dated_prices) < 5:
            return None

        dated_prices.sort(key=lambda x: x[0])
        total_span_days = (dated_prices[-1][0] - dated_prices[0][0]).days
        total_years = max(total_span_days / 365.0, 0.01)

        # ── Weekly resampling for drift accuracy ─────────────────
        weekly_returns: list[float] = []
        if total_span_days >= 28:
            weekly_buckets: dict[tuple, tuple] = {}
            for dt_val, price in dated_prices:
                iso_year, iso_week, _ = dt_val.isocalendar()
                week_key = (iso_year, iso_week)
                weekly_buckets[week_key] = (dt_val, price)

            sorted_weeks = sorted(weekly_buckets.keys())
            for i in range(1, len(sorted_weeks)):
                _, prev_price = weekly_buckets[sorted_weeks[i - 1]]
                _, curr_price = weekly_buckets[sorted_weeks[i]]
                if prev_price > 0 and curr_price > 0:
                    weekly_returns.append(math.log(curr_price / prev_price))

        # Time-scaled daily returns (fallback + jump detection)
        daily_scaled_returns: list[float] = []
        for i in range(1, len(dated_prices)):
            delta_days = (dated_prices[i][0] - dated_prices[i - 1][0]).days
            if delta_days <= 0:
                continue
            lr = math.log(dated_prices[i][1] / dated_prices[i - 1][1])
            daily_scaled_returns.append(lr / math.sqrt(delta_days))

        # Choose best estimation method
        if len(weekly_returns) >= 8:
            mu_est = statistics.mean(weekly_returns) * 52
            sigma_est = statistics.stdev(weekly_returns) * math.sqrt(52)
            n_obs = len(weekly_returns)
            method = "weekly_resampling"
        elif len(daily_scaled_returns) >= 5:
            mu_est = statistics.mean(daily_scaled_returns) * 365
            sigma_est = statistics.stdev(daily_scaled_returns) * math.sqrt(365)
            n_obs = len(daily_scaled_returns)
            method = "time_scaled_daily_fallback"
        else:
            return None

        # ── Jump detection on time-scaled returns ────────────────
        n_jumps = 0
        lambda_jump = 2.0
        mu_j = -0.05
        sigma_j = 0.10

        if len(daily_scaled_returns) >= 5:
            sigma_scaled = statistics.stdev(daily_scaled_returns)
            threshold = 2.0 * sigma_scaled
            jump_scaled = [r for r in daily_scaled_returns if abs(r) > threshold]
            n_jumps = len(jump_scaled)
            lambda_jump = n_jumps / total_years if total_years > 0 else 2.0
            lambda_jump = max(0.5, min(lambda_jump, 20.0))

            if n_jumps >= 2:
                mu_j = statistics.mean(jump_scaled)
                sigma_j = statistics.stdev(jump_scaled)
            elif n_jumps == 1:
                mu_j = jump_scaled[0]
                sigma_j = abs(jump_scaled[0]) * 0.5

        # ── Standard errors ──────────────────────────────────────
        mu_se: Optional[float] = None
        sigma_se: Optional[float] = None

        if method == "weekly_resampling" and len(weekly_returns) > 1:
            w_sigma = statistics.stdev(weekly_returns)
            mu_se = w_sigma / math.sqrt(len(weekly_returns)) * 52
            sigma_se = w_sigma * math.sqrt(52) / math.sqrt(2 * (len(weekly_returns) - 1))
        elif len(daily_scaled_returns) > 1:
            d_sigma = statistics.stdev(daily_scaled_returns)
            mu_se = d_sigma / math.sqrt(len(daily_scaled_returns)) * 365
            sigma_se = d_sigma * math.sqrt(365) / math.sqrt(2 * (len(daily_scaled_returns) - 1))

        lambda_se = math.sqrt(lambda_jump / total_years) if total_years > 0 else None

        # ── Mean-reversion detection ─────────────────────────────
        mean_reversion_score: Optional[float] = None
        series = weekly_returns if len(weekly_returns) >= 10 else (
            daily_scaled_returns if len(daily_scaled_returns) >= 10 else []
        )
        if len(series) >= 10:
            mean_s = statistics.mean(series)
            demeaned = [r - mean_s for r in series]
            numerator = sum(demeaned[i] * demeaned[i + 1] for i in range(len(demeaned) - 1))
            denominator = sum(d**2 for d in demeaned)
            if denominator > 0:
                mean_reversion_score = round(numerator / denominator, 4)

        # Sanity clamps
        sigma_est = max(0.10, min(sigma_est, 3.0))
        mu_est = max(-1.0, min(mu_est, 2.0))
        mu_j = max(-0.50, min(mu_j, 0.50))
        sigma_j = max(0.01, min(sigma_j, 0.50))

        return {
            "mu_annual": round(mu_est, 4),
            "sigma_annual": round(sigma_est, 4),
            "jump_intensity_lambda": round(lambda_jump, 4),
            "jump_mean_mu_j": round(mu_j, 4),
            "jump_vol_sigma_j": round(sigma_j, 4),
            "param_source_detail": method,
            "data_points": len(dated_prices),
            "observation_span_days": total_span_days,
            "jumps_detected": n_jumps,
            "param_confidence": {
                "mu_se": round(mu_se, 4) if mu_se is not None else None,
                "sigma_se": round(sigma_se, 4) if sigma_se is not None else None,
                "lambda_se": round(lambda_se, 4) if lambda_se is not None else None,
            },
            "mean_reversion": {
                "lag1_autocorrelation": mean_reversion_score,
                "interpretation": (
                    "Strong mean-reversion" if mean_reversion_score is not None and mean_reversion_score < -0.3
                    else "Mild mean-reversion" if mean_reversion_score is not None and mean_reversion_score < -0.1
                    else "Trend-following" if mean_reversion_score is not None and mean_reversion_score > 0.1
                    else "Random walk" if mean_reversion_score is not None
                    else "Insufficient data"
                ),
            } if mean_reversion_score is not None else None,
        }

    except Exception:
        logger.exception("Calibration failed")
        return None

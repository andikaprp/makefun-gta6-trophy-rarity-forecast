"""gta6-trophy-rarity-forecast-01 entry: a structural forecaster for Exophase earn rates.

The idea, in one paragraph. A trophy's earn rate is earners / players_tracked.
The two move for different reasons - the tracked pool grows as the game sells,
earners grow as the players who are already tracked reach the trophy - and the
page only changes when Exophase refreshes it, so the history is a step series
of (players, rates) states. This program collapses the history to its distinct
states, drops the stale regime before the largest pool jump (the launch-night
pool replacement), then forecasts the two parts separately on the launch clock
x = ln(1 + hours since 2026-11-19T00:00:00Z): the pool by a log-linear fit, each
trophy's earners by a log-linear fit of its own (earners can only grow, and the
fit is floored at the last count), and blends the resulting rate with a direct
logit-trend extrapolation of the rate itself. The spread combines the house
random walk's horizon term, the trophy's own residual volatility around its
trend, and a floor for the refresh steps no history can time.

Standard library only, deterministic: the same case always yields the same
bytes. Run it the way the grader does:
    MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z_TABLE = {0.05: -1.645, 0.25: -0.674, 0.5: 0.0, 0.75: 0.674, 0.95: 1.645}
EPOCH = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)

# Model constants (see MODEL.md; chosen on structure, checked by simulation).
W_DECOMP = 0.4         # weight of the earners/pool decomposition in the median
E_DAMP = 1.0           # damping on the earners extrapolation distance
T_DAMP = 0.3           # damping on the rate-trend extrapolation distance
N_DAMP = 1.0           # damping on the pool extrapolation distance
RW_COEF = 0.38         # random-walk spread per sqrt-day of horizon, logit units
RESID_COEF = 0.8       # weight of a trophy's own trend-residual volatility
SPREAD_FLOOR = 0.30    # refresh-step floor on the spread, logit units
SPREAD_MIN = 0.35
SPREAD_MAX = 2.2
FALLBACK_SPREAD = 0.95  # spread when there is no usable trend (e.g. practice)


def clamp_rate(x):
    return min(99.99, max(0.01, x))


def logit(pct):
    p = clamp_rate(pct) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit(lg):
    if lg >= 0:
        return 100.0 / (1.0 + math.exp(-lg))
    e = math.exp(lg)
    return 100.0 * e / (1.0 + e)


def round4(x):
    scaled = x * 10000
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 10000
    return int(value) if float(value).is_integer() else value


def z_for(tau):
    if tau in Z_TABLE:
        return Z_TABLE[tau]
    # Acklam's rational approximation of the normal quantile, for odd levels.
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    dd = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
          3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if tau < plow:
        q = math.sqrt(-2 * math.log(tau))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((dd[0] * q + dd[1]) * q + dd[2]) * q + dd[3]) * q + 1)
    if tau > phigh:
        q = math.sqrt(-2 * math.log(1 - tau))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((dd[0] * q + dd[1]) * q + dd[2]) * q + dd[3]) * q + 1)
    q = tau - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def clock_x(t):
    """The launch clock: ln(1 + hours since 2026-11-19T00:00:00Z)."""
    hours = (t - EPOCH).total_seconds() / 3600.0
    return math.log(1.0 + max(-0.9, hours))


def wls(xs, ys, ws):
    """Weighted least squares y ~ a + b x; returns (a, b, residual sigma)."""
    sw = sum(ws)
    mx = sum(w * x for x, w in zip(xs, ws)) / sw
    my = sum(w * y for y, w in zip(ys, ws)) / sw
    sxx = sum(w * (x - mx) ** 2 for x, w in zip(xs, ws))
    if sxx <= 1e-12:
        return my, 0.0, 0.0
    sxy = sum(w * (x - mx) * (y - my) for x, y, w in zip(xs, ys, ws))
    b = sxy / sxx
    a = my - b * mx
    dof = max(1.0, sw - 2.0) if sw > 2 else 1.0
    var = sum(w * (y - (a + b * x)) ** 2 for x, y, w in zip(xs, ys, ws)) / dof
    return a, b, math.sqrt(max(0.0, var))


def distinct_states(case):
    """Collapse consecutive reads with identical (players, rates) into states.

    Each state keeps the slot of its first read and how long it persisted
    (hours until the next distinct state, or until the case checkpoint).
    """
    states = []
    for read in case["history"]:
        key = (read["players_tracked"], tuple(r if r is not None else -1.0 for r in read["rates"]))
        if states and states[-1]["key"] == key:
            continue
        states.append({"key": key, "slot": parse_time(read["slot"]),
                       "players": read["players_tracked"], "rates": read["rates"]})
    end = parse_time(case["at"])
    for i, s in enumerate(states):
        nxt = states[i + 1]["slot"] if i + 1 < len(states) else end
        s["persist_h"] = max(0.05, (nxt - s["slot"]).total_seconds() / 3600.0)
    return states


def regime_start(states):
    """Index after the largest single pool jump of 3x or more (a pool
    replacement, like launch night's 638 -> 3,868); 0 if there is none."""
    start = 0
    for i in range(1, len(states)):
        prev, cur = states[i - 1]["players"], states[i]["players"]
        if prev and cur and cur >= 3.0 * prev:
            start = i
    return start


def weights_for(states):
    last_t = states[-1]["slot"]
    ws = []
    for s in states:
        age_h = max(0.0, (last_t - s["slot"]).total_seconds() / 3600.0)
        recency = math.exp(-age_h / 36.0)
        ws.append(min(6.0, s["persist_h"]) * (0.2 + 0.8 * recency))
    return ws


def forecast_case(case):
    levels = case.get("levels") or LEVELS
    zs = [z_for(t) for t in levels]
    trophies = case["trophies"]
    d_days = max(0.0, case["horizon_minutes"] / 1440.0)
    target_x = clock_x(parse_time(case["target"]))
    states = distinct_states(case)
    last = case["history"][-1]
    last_rates = []
    for i in range(len(trophies)):
        r = last["rates"][i]
        if r is None:  # carry the last non-null rate backwards
            for read in reversed(case["history"]):
                if read["rates"][i] is not None:
                    r = read["rates"][i]
                    break
        last_rates.append(r if r is not None else 0.0)

    def flat_rows(spread):
        return [{"id": t["id"],
                 "q": [round4(inv_logit(logit(r) + z * spread)) for z in zs]}
                for t, r in zip(trophies, last_rates)]

    fit_states = states[regime_start(states):] if states else []
    if len(fit_states) < 3:
        return {"trophies": flat_rows(max(FALLBACK_SPREAD, RW_COEF * math.sqrt(d_days)))}
    # Fit on the recent window: the pool and the players in it turn over fast
    # in week one, and the last two days of reads carry the current regime.
    # (The house curve baseline fits only the last 24 hours; 48 keeps more
    # states while staying current.)
    last_slot = fit_states[-1]["slot"]
    recent = [s for s in fit_states
              if (last_slot - s["slot"]).total_seconds() <= 48 * 3600]
    if len(recent) >= 4:
        fit_states = recent

    xs = [clock_x(s["slot"]) for s in fit_states]
    ws = weights_for(fit_states)
    x_last = xs[-1]
    # Growth bends: a linear fit read at the full horizon overshoots a
    # saturating process, so each extrapolation is read at a damped target.
    x_eff_e = x_last + (target_x - x_last) * E_DAMP
    x_eff_t = x_last + (target_x - x_last) * T_DAMP
    x_eff_n = x_last + (target_x - x_last) * N_DAMP
    # Denominator: log-linear pool fit, floored at the last count.
    a_n, b_n, _ = wls(xs, [math.log(max(1, s["players"])) for s in fit_states], ws)
    n_last = max(1, fit_states[-1]["players"])
    n_hat = math.exp(a_n + b_n * x_eff_n)
    n_hat = min(max(n_hat, float(n_last)), float(n_last) * 40.0)

    horizon_spread = RW_COEF * math.sqrt(d_days)
    rows = []
    for i, t in enumerate(trophies):
        pts = [(x, s) for x, s in zip(xs, fit_states) if s["rates"][i] is not None]
        r_last = last_rates[i]
        if len(pts) < 3:
            med = logit(r_last)
            spread = max(FALLBACK_SPREAD, horizon_spread)
        else:
            px = [p[0] for p in pts]
            pw = [ws[j] for j, s in enumerate(fit_states) if s["rates"][i] is not None]
            # Earners path: log-linear in the launch clock, floored at the last count.
            earn = [max(0.02, p[1]["rates"][i] * p[1]["players"] / 100.0) for p in pts]
            a_e, b_e, _ = wls(px, [math.log(e) for e in earn], pw)
            e_hat = math.exp(a_e + b_e * x_eff_e)
            e_last = max(0.02, r_last * n_last / 100.0)
            e_hat = min(max(e_hat, e_last), 0.999 * n_hat)
            m_dec = 100.0 * e_hat / n_hat
            # Direct trend of the rate on the logit scale.
            a_r, b_r, sig_r = wls(px, [logit(p[1]["rates"][i]) for p in pts], pw)
            m_tr = inv_logit(a_r + b_r * x_eff_t)
            med = W_DECOMP * logit(m_dec) + (1.0 - W_DECOMP) * logit(m_tr)
            spread = math.sqrt(horizon_spread ** 2 + (RESID_COEF * sig_r) ** 2 + SPREAD_FLOOR ** 2)
            spread = min(max(spread, SPREAD_MIN), SPREAD_MAX)
        rows.append({"id": t["id"], "q": [round4(inv_logit(med + z * spread)) for z in zs]})
    return {"trophies": rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        try:
            out = forecast_case(case)
        except Exception:
            # Never crash a case: fall back to persistence with a wide spread.
            last = case["history"][-1]
            d = max(0.0, case["horizon_minutes"] / 1440.0)
            spread = max(FALLBACK_SPREAD, RW_COEF * math.sqrt(d))
            rows = []
            for t, r in zip(case["trophies"], last["rates"]):
                rr = r if r is not None else 0.0
                rows.append({"id": t["id"],
                             "q": [round4(inv_logit(logit(rr) + z * spread))
                                   for z in [z_for(tau) for tau in (case.get("levels") or LEVELS)]]})
            out = {"trophies": rows}
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

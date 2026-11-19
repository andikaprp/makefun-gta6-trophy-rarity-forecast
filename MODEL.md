# Model — how `main.py` forecasts

## Shape of the problem

The grader runs the program once per case. A case gives every Exophase read before a checkpoint
(the final's history runs from launch night to the deadline, a read every 10 minutes) and asks
for five quantiles (5/25/50/75/95%) of each trophy's earn rate at the target — for the final,
2026-11-26T00:00:00Z, 4.52 days past the last read. Scoring is the mean pinball loss on the
logit scale against the realized read (see `REQUIREMENTS.md`); a prize needs a loss strictly
below the best house entry's.

A rate is `100 × earners / players_tracked`, and the two parts obey different laws: the pool
grows as the game sells (and dilutes), earners only ever grow as the tracked players progress.
The house baseline ignores both (persistence plus a random-walk spread); the house curve
baseline extrapolates the rate's own logit trend over the last 24 hours, which nets the two
effects but cannot see them change. This program forecasts the parts separately, then hedges
the result with a damped version of the rate trend.

## The method, step by step (all from the case itself; no external data)

1. **Distinct states.** Consecutive reads with identical (players, rates) are one observation
   of the page; the series collapses to its distinct states, each weighted by how long it
   persisted and by recency (36-hour decay).
2. **Regime cut.** The fit starts after the largest single pool jump of 3× or more — a pool
   replacement like launch night's 638 → 3,868 — because the pool before it is a different
   population (pre-release accounts).
3. **Recent window.** Fits use the last 48 hours of states (the house curve uses 24; 48 keeps
   more states while staying in the current regime).
4. **Pool.** Weighted least squares of ln(players) on the launch clock
   `x = ln(1 + hours since 2026-11-19T00:00Z)`; the target pool is the fit read at the target,
   floored at the last count and capped at 40× it.
5. **Earners.** Per trophy, weighted least squares of ln(earners + floor) on x, earners being
   rate × players / 100. The forecast is floored at the last earner count (earners cannot
   shrink) and capped just under the forecast pool.
6. **Rate trend.** Per trophy, weighted least squares of logit(rate) on x — but read at a
   **damped target**: `x_last + 0.3 × (x_target − x_last)`. Growth bends as a trophy's
   remaining earners deplete; an undamped straight line systematically overshoots, in both
   directions (this one change was worth more than everything else in simulation).
7. **Median.** On the logit scale: `0.4 × logit(earners/pool rate) + 0.6 × logit(damped trend)`.
8. **Spread.** Per trophy:
   `sqrt( (0.38·sqrt(horizon_days))² + (0.8·σ_resid)² + 0.30² )`, clamped to [0.35, 2.2],
   where σ_resid is the trophy's residual volatility around its own trend fit. The three terms
   are the horizon walk, the trophy's observed noisiness, and a floor for the refresh steps
   nobody can time. Quantiles are the logit-normal read of (median, spread) at the five levels,
   rounded to 4 decimals like the house's.
9. **Fallbacks.** Fewer than 3 usable states (the practice cases, whose histories are one
   frozen page) → persistence median with a wide spread (0.95), because an unseen refresh is
   the dominant risk there. Any unexpected failure inside a case → the same fallback rather
   than a crash: an invalid case forfeits the prize by itself.

Deterministic throughout (no randomness, no clock reads beyond the case's own timestamps),
standard library only, ~0.0 s per case against an 8-second budget.

## How it was checked (no local grade exists)

- `score.py` reimplements the scoring rule exactly and reproduces all three worked numbers in
  RULES.txt (0.403404; 0.46023; the baseline's 1.10% final row [0.1931, 0.5403, 1.1, 2.2264,
  6.0088]).
- `simulate.py` builds synthetic final cases from a generative launch week anchored on the
  two real page states we hold (the frozen launch-night page and the live page of
  2026-11-19T18:07Z), under three regimes: base, hostile (fast pool growth, slow progression)
  and benign (slow pool, fast progression). On 40 fresh draws per regime the program's mean
  loss was **0.201 / 0.190 / 0.226** against the house baseline's **0.260 / 0.236 / 0.273** and
  the house curve's **0.251 / 0.242 / 0.266** — lowest in 118 of 120 draws.
- The practice outputs (`out1/p00.out`, `out1/p01.out`) validate against the format checker
  and are byte-identical across runs.

## Known limits, stated plainly

- The simulation's dynamics are my model of the process, not the process: real refreshes,
  pool-composition churn and the Nov 22–23 weekend sit outside it. The spread was therefore
  **not** tuned down to the simulation's (too smooth) noise level; it stays at house scale.
- The third house entry — the AI model forecast, one recorded superbot call on 2026-11-21 —
  cannot be replicated or scored locally. The bar it sets is the real unknown; this program's
  edge over it is that it actually fits the ~350-read history per trophy instead of eyeballing
  the same data once.
- If Exophase's tracking pool changes character mid-week (a second pool replacement), the
  regime cut handles a jump inside the history; a jump after the deadline is unforecastable
  by anyone, the baselines included.

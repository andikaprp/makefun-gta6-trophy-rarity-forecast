# Model v2 — how `main.py` forecasts

Identical to MODEL.md except the two recalibrated constants marked
**v2**. The method is unchanged; the evidence for the two changes is in
IMPROVEMENT2.md.

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
   directions.
7. **Median.** On the logit scale: `0.2 × logit(earners/pool rate) + 0.8 × logit(damped trend)`.
   **v2:** the decomposition weight was 0.4 in v1. Paired Monte Carlo on two dynamics sets
   (600 draws) preferred less decomposition weight at every step tested (0.4 → 0.3 → 0.2,
   monotone, winning every paired draw at 0.2); the decomposition is retained, not removed —
   its floors and caps still guard the median against trend whipsaw.
8. **Spread.** Per trophy:
   `sqrt( (0.38·sqrt(horizon_days))² + (0.8·σ_resid)² + 0.30² )`, clamped to [0.35, 2.2],
   where σ_resid is the trophy's residual volatility around its own trend fit. The three terms
   are the horizon walk, the trophy's observed noisiness, and a floor for the refresh steps
   nobody can time. Quantiles are the logit-normal read of (median, spread) at the five levels,
   rounded to 4 decimals like the house's.
9. **Fallbacks.** Fewer than 3 usable states (the practice cases, whose histories are one
   frozen page) → persistence median with a wide spread (**v2:** 1.3, was 0.95 — the two real
   held-out transitions that exist both score monotonically better as the fallback spread
   widens toward the game's realized ~13-hour logit moves; see IMPROVEMENT2.md), because an
   unseen refresh is the dominant risk there. Any unexpected failure inside a case → the same
   fallback rather than a crash: an invalid case forfeits the prize by itself.

Deterministic throughout (no randomness, no clock reads beyond the case's own timestamps),
standard library only, ~0.0 s per case against an 8-second budget.

## How it was checked

- `score.py` reimplements the scoring rule exactly and reproduces all three worked numbers in
  RULES.txt (unchanged from v1).
- v1's simulation check stands (MODEL.md): mean loss 0.201 / 0.190 / 0.226 vs the house
  baseline's 0.260 / 0.236 / 0.273 and the house curve's 0.251 / 0.242 / 0.266.
- v2's recalibration is checked in IMPROVEMENT2.md: paired Monte Carlo on the original
  harness (original and fresh seeds) and on a final-shaped harness anchored on the real
  2026-11-20T06:52Z page state across the three feasible pool scenarios, plus the two real
  held-out backtests the recorded states support. Headline: v2 mean loss 0.1916 vs v1's
  0.2052 on the original harness (paired wins 120/120), 0.214–0.218 vs 0.254–0.270 on the
  fresh-anchored harness (360/360), and 1.7450 / 0.6343 vs v1's 1.9670 / 0.6683 on the real
  backtests (which exercise the fallback path only — the structural limit is stated there).
- Practice outputs from `main.py` validate against the format checker and are
  byte-identical across runs.

## Known limits, stated plainly

- Everything in MODEL.md's list, plus: the W_DECOMP evidence is simulation-only. Both Monte
  Carlo harnesses share one generative family (a logit-space pull of each trophy's share
  toward an asymptote), which structurally favors the trend component the change leans on.
  No real held-out test of the trend machinery exists: only three post-launch page states
  were ever recorded outside the house's sealed dataset, and every backtest they support
  runs through the fallback. The change is one constant, one step, with the decomposition
  retained — sized to the strength of that evidence, not beyond it.
- The third house entry — the AI model forecast, one recorded superbot call on 2026-11-21 —
  cannot be replicated or scored locally. The bar it sets is the real unknown; this program's
  edge over it is that it actually fits the ~350-read history per trophy instead of eyeballing
  the same data once.

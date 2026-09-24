# Verification test plan: EM chart calculations vs. the real player aids

The sustained-turn / EM chart feature (`get_sustained_load()`,
`find_best_sustained_turn()`, `find_structural_corner_speed()`,
`phad_cells_from_load()`) layers new, derived analysis on top of the game's
existing per-turn calculation. Some of it corresponds directly to values a
player reads off the physical E6B-style slide rule and the printed Aircraft
Data Card (ADC) tables; some of it (sustained load, corner speed) is purely
derived and has no dial of its own, so it needs a different verification
method; and PHAD cells needs an actual rulebook/play example, not the slide
rule, since we've already found one internal contradiction there once.

**How to verify each category:**

- **Base conversions & table lookups (1-6, 9)** -- read the value straight
  off the slide rule / printed ADC table.
- **Full turn resolution (7-8)** -- run the actual slide-rule turn
  procedure step by step and compare the resulting new speed.
- **Sustained load (10-12)** -- there's no dial for this. Verify
  empirically: take the load speedbop claims is "sustained" at that speed,
  run it through the real turn procedure for one full turn, and confirm
  the new KTAS comes back approximately equal to the starting KTAS (no net
  gain or loss).
- **Corner speed (13-14)** -- cross-check against the ADC's printed
  structural G rating directly.
- **PHAD cells (15-16)** -- needs actual logged play examples or the
  rulebook text, not the slide rule.

## Test cases

| # | What it verifies | Scenario | speedbop's current output | Check against |
|---|---|---|---|---|
| 1 | KEAS/Q/Smash/Mach at alt 0 (KEAS=KTAS) | FJ-3M, weight 15.7, alt 0, 400 KTAS | KEAS=400, Q=54.2, Smash=10.4, Mach=0.6 | Slide rule |
| 2 | Same, at altitude (KEAS != KTAS) | Swift Mk5, weight 15.8, alt 75, 450 KTAS | KEAS=350, Q=41.5, Smash=8.7, Mach=0.7 | Slide rule |
| 3 | LCS/IDS/form-drag table lookup | FJ-3M @ mach 0.6 | LCS=4.7, IDS=328, form drag=19 | ADC printed tables |
| 4 | Engine output isobar interpolation | Swift Mk5 @ mach 0.7, alt 75 | dry=40.9, AB=60.1 | ADC engine chart |
| 5 | Structural/lift-limited max load | FJ-3M, case 1 (400kt/alt 0) | max_load=49 (16.3G) | ADC alpha_max + slide rule |
| 6 | Same, different aircraft/altitude | Swift Mk5, case 2 (450kt/alt 75) | max_load=39 (13G) | ADC alpha_max + slide rule |
| 7 | Full turn resolution, dry | FJ-3M, weight 17.4, alt 75, 485kt, 20 pulls, dAlt=0 | (run and record new KTAS) | Manual slide-rule turn |
| 8 | Full turn resolution, AB | Swift Mk5, weight 15.8, alt 75, 400kt, 15 pulls, dAlt=0, AB on | (run and record new KTAS) | Manual slide-rule turn, AB engine table |
| 9 | Near-stall regime: lift collapses faster than energy margin | FJ-3M, weight 17.4, alt 75, 65kt | max_load=0, sustained_load=2.51 | Slide rule (confirm no G available near stall regardless of thrust) |
| 10 | Sustained load, rising side | FJ-3M dry, weight 17.4, alt 75, 250kt | sustained_load=7.28 | Pull 7 loads for one turn, expect approximately 250kt after |
| 11 | Sustained load, at the peak | FJ-3M dry, weight 17.4, alt 75, 465kt | sustained_load=12.47 (curve's max) | Pull 12 loads, expect approximately 465kt after |
| 12 | Sustained load, declining/transonic side | FJ-3M dry, weight 17.4, alt 75, 550kt | sustained_load=6.86 (well below the peak) | Pull 7 loads, expect approximately 550kt after -- confirms the peak-and-decline is real, not a bug |
| 13 | Literature corner speed | FJ-3M, combat_safe_load=21 | 246 KEAS (approximately 316 KTAS @ alt 75) | ADC's stated G rating + slide rule solve |
| 14 | Sustained x structural crossing ("Sustained" marker) | FJ-3M dry, weight 17.4, alt 75 | approximately 188 KTAS / 5.65 loads | Confirm sustained_load approximately equals max_load independently at that speed |
| 15 | PHAD cells, example A | 24 load @ 12 FP | 4.0 cells (post factor-of-2 fix) | Rulebook / logged play example |
| 16 | PHAD cells, example B | 8 load @ 8 FP | 2.0 cells | Rulebook / logged play example |

## Notes / flags

- **#9** looks backwards at first glance (`max_load=0` while
  `sustained_load=2.51`), but it's expected: near stall, available lift
  collapses faster than the energy margin does, so structure (not thrust)
  becomes the binding constraint.
- **#15/#16** are the two examples that once produced a contradiction
  under any single linear formula (resolved by trusting the newer,
  more specific one -- see git history on `phad_cells_from_load`). A
  third independent example from the rulebook would be the strongest way
  to close this out for good, since two points can always fit a line
  whether or not it's the correct one.
- **#2's early manual result** (wing loading 52.3, safe load 21) matches
  FJ-3M's own numbers exactly, not Swift Mk5's (which should be wing
  loading ~47.9, safe load 23) -- double check which ADC was used for
  that pass before trusting the rest of its readings.

## Calibrating the E6B conversion scales (keas/q/mach/smash)

`get_keas()`, `get_q()`, `get_mach()`, and `get_smash()` are not physics
derivations -- they're closed-form curves fit against sample readings taken
off the physical E6B (see `e6b/fit.py` and the data in `e6b/*.csv` and
`e6b/samples/*.csv`). Refitting against that existing data now (full
floating-point precision, not the hand-transcribed constants in
`speedbop.py`) found:

- **`smash = 10*q/wl`** is an exact match against all 38 `e6b/smash.csv`
  samples (0% error) -- nothing to recalibrate here.
- **`get_keas()`**'s hardcoded coefficient (0.003358) is already
  essentially optimal against `e6b/samples/keas.csv` -- a fresh refit only
  moves it to 0.0033626 (<0.1% different) and the fit's mean error is
  identical either way (~1.3%). BUT one sample row looks like a data-entry
  typo: `altitude=175, ktas=500, keas=222` -- its neighbors at the same
  altitude (300->168, 650->352) both formula and interpolation predict
  ~272-273 there, not 222. Worth re-reading that exact point on the
  physical device to confirm before we touch the CSV.
  - Also: **altitude=0 is a free, exact precision check** -- by definition
    keas=ktas there (`exp(0.003358*0)=1`), so any deviation you read off
    the physical device at altitude 0 is pure instrument/reading error,
    not a formula problem. Case #1's 396-vs-400 reading is exactly that:
    it tells you your own achievable reading precision is roughly 1%, an
    error bar worth applying to every other reading too.
  - Residual error isn't random noise -- it's smallest at altitude 0-25
    and 150-175, worse at 75-125 (~1.8%) and worst at 200 (~3-3.6%),
    meaning a single exponential term doesn't fully capture the real
    curve's shape at the high-altitude end.
- **`get_q()`**'s hardcoded formula (`keas^2/2950`) is good (<0.5% error)
  against 8 of `e6b/q.csv`'s 9 samples; the one outlier (keas=52, q=1,
  8.3% error) is almost certainly just coarse rounding at the scale's
  lowest printed gradation (q recorded to 1 significant figure there),
  not a formula problem.
- **`get_mach()`** has the most headroom: 1-4% error spread across all 17
  `e6b/mach.csv` samples, no single outlier to blame. A free refit's
  leading constant (0.001654) differs meaningfully from the hardcoded one
  (1/674.6=0.001482) -- an ~11% difference -- though it's hard to know
  whether that's the "truer" constant or just this fit absorbing noise
  from only 17 points spread across a very wide mach range (0.2-2.0),
  including the genuinely nonlinear transonic/supersonic region.

**Recommended next steps, in order of effort:**
1. Re-read the suspected keas.csv typo point on the physical device and
   correct it if confirmed.
2. For mach specifically (the biggest remaining gap), gather more sample
   points -- especially filling in the 0.9-1.3 mach range more densely --
   so `python -m e6b.fit e6b/mach.csv --output mach --linear alt --holdout 3`
   has enough coverage to refit with confidence.
3. If a single formula still can't get under ~1% across the full range
   after that, switch that scale to a lookup table with interpolation
   instead -- the same pattern the game already uses for the LCS/IDS and
   engine-output tables -- rather than continuing to fight a closed-form
   fit against a scale that isn't a clean power law everywhere.

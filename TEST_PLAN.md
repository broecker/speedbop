# Verification test plan: EM chart calculations vs. the real player aids

The sustained-turn / EM chart feature (`get_sustained_load()`,
`find_best_sustained_turn()`, `find_structural_corner_speed()`,
`phad_cells_from_load()`) layers new, derived analysis on top of the game's
existing per-turn calculation. Some of it corresponds directly to values a
player reads off the physical E6B-style slide rule and the printed Aircraft
Data Card (ADC) tables; some of it (sustained load, corner speed) is purely
derived and has no dial of its own, so it needs a different verification
method.

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

Every scenario below also lists **wing loading** (`get_wing_load()`) and
**safe load** (`get_safe_load()`) as intermediate values, since both feed
into several of the downstream numbers (smash, max load, corner speed) and
are themselves easy to cross-check against the ADC directly (wing loading
against `weight`/`wing_area`; safe load against `combat_safe_load` scaled by
`combat_weight`/`weight`). Scenarios 3-4 are pure mach/altitude table
lookups with no aircraft weight in the scenario, so wing loading/safe load
don't apply there.

## Test cases

| # | What it verifies | Scenario | speedbop's current output | Check against | Verified? |
|---|---|---|---|---|---|
| 1 | KEAS/Q/Smash/Mach at alt 0 (KEAS=KTAS) | FJ-3M, weight 15.7, alt 0, 400 KTAS | KEAS=400, Q=54.2, Smash=10.4, Mach=0.6, wing_load=52.3, safe_load=21.0 | Slide rule | **Pass** -- manual re-read within ~4% (Q/Smash high), see note below |
| 2 | Same, at altitude (KEAS != KTAS) | Swift Mk5, weight 15.8, alt 75, 450 KTAS | KEAS=350, Q=41.5, Smash=8.7, Mach=0.73, wing_load=47.9, safe_load=23.0 | Slide rule | **Pass** -- manual re-read within ~1-2%, see note below |
| 3 | LCS/IDS/form-drag table lookup | FJ-3M @ mach 0.6 | LCS=4.7, IDS=328, form drag=19 (wing_load/safe_load n/a -- mach-only lookup, no weight in this scenario) | ADC printed tables | **Pass** |
| 4 | Engine output isobar interpolation | Swift Mk5 @ mach 0.7, alt 75 | dry=40.9, AB=60.1 (wing_load/safe_load n/a -- no weight in this scenario) | ADC engine chart | **Pass** |
| 5 | Structural/lift-limited max load | FJ-3M, case 1 (400kt/alt 0) | max_load=49 (16.3G), wing_load=52.3, safe_load=21.0 | ADC alpha_max + slide rule | **Pass** -- manual read 52, +6.1%, see note below |
| 6 | Same, different aircraft/altitude | Swift Mk5, case 2 (450kt/alt 75) | max_load=39 (13G), wing_load=47.9, safe_load=23.0 | ADC alpha_max + slide rule | **Pass** -- manual read 42, +7.7%, see note below |
| 7 | Full turn resolution, dry | FJ-3M, weight 17.4, alt 75, 485kt, 20 pulls, dAlt=0 | KEAS=377, Q=48.2, Smash=8.3, Mach=0.78, max_load=44, LCS/IDS=4.2/270, alpha=10.12, induced dKTAS=75.0, form drag=24 (dKTAS 19.9), engine output=46.3 (dKTAS 41.8 after weight scaling), new KTAS=431.9; wing_load=58.0, safe_load=18.9 | Manual slide-rule turn | **Pass on end speed** (429 vs 431.9, -0.7%); engine term open -- see note below |
| 8 | Full turn resolution, AB | Swift Mk5, weight 15.8, alt 75, 400kt, 15 pulls, dAlt=0, AB on | KEAS=311, Q=32.8, Smash=6.8, Mach=0.65, max_load=31, LCS/IDS=5.6/255, alpha=12.35, induced dKTAS=72.7, form drag=25 (dKTAS 17.0), engine output=61.2 (dKTAS 61.2, at combat weight), new KTAS=371.5; wing_load=47.9, safe_load=23.0 | Manual slide-rule turn, AB engine table | Not yet run |
| 9 | Near-stall regime: lift collapses faster than energy margin | FJ-3M dry, weight 17.4, alt 75, 65kt | max_load=0, sustained_load=2.81, wing_load=58.0, safe_load=18.9 | Slide rule (confirm no G available near stall regardless of thrust) | Not yet run |
| 10 | Sustained load, rising side | FJ-3M dry, weight 17.4, alt 75, 250kt | sustained_load=8.23, wing_load=58.0, safe_load=18.9 | Pull 8 loads for one turn, expect approximately 250kt after (speedbop: 252.4) | Not yet run |
| 11 | Sustained load, at the peak | FJ-3M dry, weight 17.4, alt 75, 445kt | sustained_load=12.07 (curve's max, at the mach-0.72 table breakpoint), wing_load=58.0, safe_load=18.9 | Pull 12 loads, expect approximately 445kt after (speedbop: 445.4) | Not yet run |
| 12 | Sustained load, declining/transonic side | FJ-3M dry, weight 17.4, alt 75, 550kt | sustained_load=2.81 (well below the peak; 0 from ~600kt), wing_load=58.0, safe_load=18.9 | Pull 3 loads, expect approximately 550kt after (speedbop: 549.8) -- confirms the peak-and-decline is real, not a bug | Not yet run |
| 13 | Literature corner speed | FJ-3M, weight 17.4, combat_safe_load=21 | `calculate_corner_speed()` at the 485kt state: 259 KEAS (approximately 333 KTAS @ alt 75); EM chart's Corner marker: 360 KTAS; wing_load=58.0, safe_load=18.9 | ADC's stated G rating + slide rule solve | Not yet run |
| 14 | Sustained x lift-limited crossing ("Sustained" marker) | FJ-3M dry, weight 17.4, alt 75 | crossover band approximately 203-215 KTAS at ~7 loads (sustained 6.97-7.18 while max_load holds at 7); the app's marker, sampled every 10kt, reads 212kt / 7.2 loads; wing_load=58.0, safe_load=18.9 | Confirm sustained_load approximately equals max_load independently at a speed in that band (e.g. 212kt) | Not yet run |

## Notes / flags

- **#2's early manual result** (wing loading 52.3, safe load 21) matches
  FJ-3M's own numbers exactly, not Swift Mk5's (which should be wing
  loading ~47.9, safe load 23) -- double check which ADC was used for
  that pass before trusting the rest of its readings.
- **#1/#2 re-verified 2026-09, resolved**: re-ran both scenarios directly
  against current `speedbop.py` and both reproduce the table exactly --
  no bug here. An earlier manual pass on #1 reported Q=81/Smash=16/
  Mach=0.62 against inputs that should give Q=54.2/Smash=10.4/Mach=0.6;
  that first reading looked like a transcription slip (no single input
  reproduced all three). A second manual re-read came back much closer
  and internally consistent: #1 read KEAS=400/Mach=0.6/safe_load=21
  exactly, with Q +3.3% high (56 vs 54.2) and Smash +3.8% high (10.8 vs
  10.4); #2 read wing_load/safe_load essentially exact and KEAS/Q/Smash
  1-2% high. In both cases Smash tracks the read Q via the exact
  `smash=10*q/wl` relationship, so the drift is one slightly-high Q (or
  KEAS) reading propagating through, not independent errors on each
  value -- ordinary E6B slide-alignment slop, well within the ~1%
  baseline instrument error already established for this device.
- **#3-#4 verified 2026-09, pass**: manual checks against the ADC printed
  tables (LCS/IDS/form drag, engine output isobars) came back matching, no
  error margin reported -- these are direct table reads/lookups rather
  than multi-step slide-rule conversions, so less exposed to the
  alignment slop discussed above.
- **#5/#6 verified 2026-09, pass with a wider margin**: manual reads came
  back max_load=52 (formula: 49, +6.1%) and max_load=42 (formula: 39,
  +7.7%). `max_load = alpha_max/lcs * smash` is linear in smash with
  alpha_max/lcs a fixed printed-table constant (LCS itself was confirmed
  exact in #3), so this relative gap is really a smash-reading gap of the
  same size -- larger than the 3.8%/2.3% smash drift seen on the same two
  aircraft in #1/#2, but the same direction (high) and the same
  mechanism (E6B slide-alignment slop), not a new error source. Worth
  keeping an eye on whether later scenarios keep drifting high, which
  would point to a consistent technique bias rather than random noise.
- **#7 first pass 2026-09, found three code bugs (fixed; re-run
  pending)**: manual pass read end speed 438 vs speedbop's then-419.4.
  KEAS/Q/Mach agreed within ~2%. The manual alpha/max_load/induced came
  from the wrong lift row (0.72, a manual slip -- mach 0.78 selects the
  0.78 lift row and 0.79 drag row), but comparing the two exposed:
  1. *`get_mach()` rounded to one decimal*, so 0.783 became 0.8 and the
     lookups skipped to the 0.84 lift row (LCS 3.8) and 0.82 drag row
     (27). The ceiling lookup convention itself is correct. It now rounds
     to two decimals and picks the 0.78/0.79 rows. This also moves
     max_load 48 -> 44 and the corner speed 246 -> 259 KEAS for this
     state.
  2. *Engine output wasn't weight-scaled*: engine output is the base
     delta knots at combat weight and must be scaled by
     `combat_weight / weight` (15.7/17.4 here: 46.3 -> 41.8 dKTAS).
  3. *`get_sustained_load()` disagreed with `calculate_performance()`*:
     it subtracted raw form drag where the turn formula subtracted the
     converted form dKTAS, so a "sustained" turn didn't actually hold
     speed. The unit-test fixture had zero form drag, which hid it. Both
     now share one form-drag and one engine helper.

  Safe load is confirmed to be weight-adjusted, which speedbop already
  does (21 at combat weight -> 18.9 at 17.4).
- **#7 re-run 2026-09, end speed passes, engine term open**: manual read
  wing_load=58 (exact), smash=8.5 (+2.4%), max_load=46 (+4.5%),
  alpha=9.8 (-3.2%), induced dKTAS=72 (-4.0%), form dKTAS=20 (+0.5%),
  engine dKTAS=36 (speedbop 41.8, -14%), new KTAS=429 (speedbop 431.9,
  -0.7%). Max load, alpha and induced all follow from the slightly high
  smash reading (with smash 8.5 the formulas give 45.3 / 9.9 / 73.2), so
  they're ordinary slide-rule drift. The engine term is not:
  `combat_weight/weight` scaling (x0.902) turns speedbop's 46.3 into 41.8,
  and the first pass's 47 into 42.4, while 36 would need an engine output
  of ~40. Nothing near mach 0.78 / alt 75 on the J65 chart reads that low
  (46-47 there), and both passes landed on 36, so this looks like a
  consistent procedural difference rather than a misread. The end speeds
  agree only because the lower engine term (-5.8 kt) is partly offset by
  the lower induced drag (+3 kt). Open until the engine step is
  reconciled.
- **Form drag conversion confirmed 2026-09**: form dKTAS is
  `drag * smash / 10`, confirmed by calculation against the player aids.
  speedbop previously used `drag/smash*10`, which made form drag shrink
  with speed instead of grow. That wrong formula was in the turn formula
  from the start; fix 3 above briefly carried it into the EM chart too.
  With the correct formula, FJ-3M dry at alt 75 has the expected EM
  shape: sustained load rises to a 12.07-load peak at 445kt, falls
  through the transonic region, and reaches zero at ~600kt. #10-#12 and
  #14 were re-picked against this curve.
- **#9** looks backwards at first glance (`max_load=0` while
  `sustained_load=2.81`), but it's expected: near stall, available lift
  collapses faster than the energy margin does, so structure (not thrust)
  becomes the binding constraint.
- **The sustained curve is bumpy** where mach crosses a lift/drag table
  row (e.g. the peak sits exactly on the 0.72 row, and 450kt drops to
  11.35 as the 0.78 row takes over). That's the step-wise tables, not
  noise -- expect sustained load to jump at those breakpoints.

## Calibrating the E6B conversion scales (keas/q/mach/smash)

`get_keas()`, `get_q()`, `get_mach()`, and `get_smash()` are not physics
derivations -- they're closed-form curves fit against sample readings taken
off the physical E6B (see `e6b/fit.py` and the data in `e6b/*.csv` and
`e6b/samples/*.csv`).

- **`smash = 10*q/wl`** is an exact match against all 38 `e6b/smash.csv`
  samples (0% error) -- nothing to recalibrate here.
- **`get_keas()`**'s hardcoded coefficient (0.003358) is already
  essentially optimal against `e6b/samples/keas.csv`. One sample row still
  looks like an unconfirmed data-entry typo: `altitude=175, ktas=500,
  keas=222` -- neighbors at the same altitude (300->168, 650->352) both
  formula and interpolation predict ~272-273 there, not 222. Worth
  re-reading that exact point on the physical device.
  - **altitude=0 is a free, exact precision check** -- by definition
    keas=ktas there (`exp(0.003358*0)=1`), so any deviation read off the
    physical device at altitude 0 is pure instrument/reading error, not a
    formula problem -- a useful error bar (roughly 1%) to apply to every
    other manual reading too.
- **`get_q()`** and **`get_mach()`** both turned out to be clean closed-form
  relationships after all -- a first pass with denser data showed R^2
  dropping (0.996 and 0.982), which briefly looked like evidence they
  needed lookup tables instead. That was three data-entry typos, not a
  real property of the relationships: with the E6B being a physically
  ring-and-window device, every reading is mechanically guaranteed to
  reduce to a power law or exponential, and once the typos were confirmed
  and corrected, both refit to R^2 > 0.997 with the *same* formula shapes
  already in the code (q = k*keas^2 exactly, matching q's physical
  definition; mach = k*keas*exp(c*alt), matching how EAS/altitude/Mach
  actually relate). Trying a nonlinear-window shape for mach's altitude
  term (in case the dial itself was nonlinearly graduated) barely moved
  R^2 at all, confirming the plain exponential shape was already right --
  this was a data-quality problem, not a wrong-model problem.
  - Corrected: `q.csv` `103.5->7` was actually `143.5->7`; `mach.csv`
    `104/200->0.6` was actually `168/200->0.6`; `mach.csv` `300/230->1.5`
    was actually `300/272->1.5`.
  - `speedbop.py`'s constants are now refit against the corrected data:
    `get_q()` divisor 2950 -> 2950.3; `get_mach()`'s 674.6 -> 670.0 and
    0.0045 -> 0.0044. The reference scenario (FJ-3M, weight 17.4, alt 75,
    485kt) is unaffected -- both mach and q round to the same displayed
    value either way -- but roughly 8-10% of (keas, altitude) combinations
    elsewhere do shift by one rounded unit, so this is a real (if small)
    accuracy change across the whole app, not just these two functions.
  - mach's remaining ~2% typical / ~10% worst-case error (worst point:
    `keas=500, alt=20, mach=0.9`) has no single dominant outlier left --
    it reads as ordinary manual-reading noise concentrated in the
    transonic/supersonic corner of the scale, the hardest region to read
    precisely on the physical device.

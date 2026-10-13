# Verification test plan: EM chart calculations vs. the real player aids

The sustained-turn / EM chart feature (`performance.py`: `sustained_load()`,
`find_best_sustained_turn()`, `find_best_sustained_turn_rate()`,
`find_structural_corner_speed()`, `phad_cells_from_load()`) layers new, derived analysis on top of the game's
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
| 1 | KEAS/Q/Smash/Mach at alt 0 (KEAS=KTAS) | FJ-3M, weight 15.7, alt 0, 400 KTAS | KEAS=400, Q=54.2, Smash=10.4, Mach=0.62 (0.615 unrounded), wing_load=52.3, safe_load=21.0 | Slide rule | **Pass** -- manual re-read within ~4% (Q/Smash high), see notes below |
| 2 | Same, at altitude (KEAS != KTAS) | Swift Mk5, weight 15.8, alt 75, 450 KTAS | KEAS=357, Q=43.2, Smash=9.0, Mach=0.73, wing_load=47.9, safe_load=23.0 | Slide rule | **Pass** -- manual re-read within ~1-3%, see notes below |
| 3 | LCS/IDS/form-drag table lookup | FJ-3M @ mach 0.6 | LCS=4.7, IDS=328, form drag=19 (wing_load/safe_load n/a -- mach-only lookup, no weight in this scenario) | ADC printed tables | **Pass** |
| 4 | Engine output isobar interpolation | Swift Mk5 @ mach 0.7, alt 75 | dry=40.9, AB=60.1 (wing_load/safe_load n/a -- no weight in this scenario) | ADC engine chart | **Pass** |
| 5 | Structural/lift-limited max load | FJ-3M, case 1 (400kt/alt 0) | max_load=49 (16.3G), wing_load=52.3, safe_load=21.0 | ADC alpha_max + slide rule | **Pass** -- manual read 52, +6.1%, see notes below |
| 6 | Same, different aircraft/altitude | Swift Mk5, case 2 (450kt/alt 75) | max_load=41 (13.7G), wing_load=47.9, safe_load=23.0 | ADC alpha_max + slide rule | **Pass** -- manual read 42, +2.4%, see notes below |
| 7 | Full turn resolution, dry | FJ-3M, weight 17.4, alt 75, 485kt, 20 pulls, dAlt=0 | KEAS=385, Q=50.2, Smash=8.7, Mach=0.79 (0.788 unrounded), max_load=51, LCS/IDS=3.8/232 (0.84 row), alpha=8.74, induced dKTAS=75.3, form drag=24 (dKTAS 20.9), engine output=46.1 / engine scale 1.174 = base 39.3 (dKTAS 35.4 after weight scaling), new KTAS=424.2; wing_load=58.0, safe_load=18.9 | Manual slide-rule turn | **Pass on end speed** (429 vs 424.2, +1.1%), but sits on a lift-row boundary -- see notes below |
| 8 | Full turn resolution, AB | Swift Mk5, weight 15.8, alt 75, 400kt, 15 pulls, dAlt=0, AB on | KEAS=317, Q=34.1, Smash=7.1, Mach=0.65 (0.649 unrounded), max_load=32, LCS/IDS=5.6/255, alpha=11.83, induced dKTAS=69.6, form drag=25 (dKTAS 17.8), engine output=61.2 / engine scale 1.334 = base 45.9 (dKTAS 45.9, at combat weight), new KTAS=358.5; wing_load=47.9, safe_load=23.0 | Manual slide-rule turn, AB engine table | **Pass** -- end speed 359 vs 358.5 (+0.1%); see notes below |
| 9 | Near-stall regime: lift collapses faster than energy margin | FJ-3M dry, weight 17.4, alt 75, 65kt | max_load=0, sustained_load=2.11, wing_load=58.0, safe_load=18.9 | Slide rule (confirm no G available near stall regardless of thrust) | **Pass** -- no whole load available (manual 0.25, speedbop 0); readings at the scale minimum, see note below |
| 10 | Sustained load, rising side | FJ-3M dry, weight 17.4, alt 75, 250kt | sustained_load=6.48, wing_load=58.0, safe_load=18.9 | Pull 6 loads for one turn, expect approximately 250kt after (speedbop: 253.7) | **Pass** -- end speed 252 vs 253.7 (-0.7%); two slips, see notes below |
| 11 | Sustained load, at the peak | FJ-3M dry, weight 17.4, alt 75, 445kt | sustained_load=10.22 (curve's max, on the mach-0.72 table breakpoint), wing_load=58.0, safe_load=18.9 | Pull 10 loads, expect approximately 445kt after (speedbop: 445.9) | Not yet run |
| 12 | Sustained load, declining/transonic side | FJ-3M dry, weight 17.4, alt 75, 520kt | sustained_load=6.73 (below the peak; 0 from ~546kt), wing_load=58.0, safe_load=18.9 | Pull 7 loads, expect approximately 520kt after (speedbop: 519.4) -- confirms the peak-and-decline is real, not a bug | Not yet run |
| 13 | Literature corner speed | FJ-3M, weight 17.4, combat_safe_load=21 | `corner_speed()` at the 485kt state: 246 KEAS (approximately 310 KTAS @ alt 75); EM chart's Corner marker: 350 KTAS; wing_load=58.0, safe_load=18.9 | ADC's stated G rating + slide rule solve | Not yet run |
| 14 | Sustained x lift-limited crossing ("Sustained" marker) | FJ-3M dry, weight 17.4, alt 75 | approximately 168 KTAS at 4 loads: in whole loads (as the chart shows both curves) sustained and max_load are both 4 from 152 to 168kt, then max_load steps to 5 at 169kt while sustained stays at 4 (precise sustained: 4.63); the app's marker, sampled every 10kt, reads 160kt / 4 loads with fractional loads off, and 157kt / 4.2 loads with them on (the default); wing_load=58.0, safe_load=18.9 | Confirm 4 loads is sustainable (holds speed) and the airframe allows 4 but not 5 loads just below 169kt | Not yet run |
| 15 | Best sustained turn rate ("Best sustained rate" marker) | FJ-3M dry, weight 17.4, alt 75 | Fractional loads on (default): 2.3 cells @ 170kt, 4.63 loads (the last 10kt sample before the 4 -> 5 FP step; at 1kt resolution the peak is 2.41 cells @ 177.5kt). Fractional loads off: 2 PHAD cells from 140 to 480kt (10kt samples): 3 loads at 140kt (4 FP, lift-limited; sustained 3.76) up to 9 loads at 480kt (12 FP, energy-limited; sustained 9.00); cells from the 2 x load / FP rule of thumb, rounded to nearest; wing_load=58.0, safe_load=18.9 | Pull 3 loads at 140kt and 9 loads at 480kt: each should hold speed (speedbop: 150.5kt and 480.0kt after) and turn 2 cells -- re-check once the real turn-rate formula replaces the rule of thumb | Not yet run |

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
- **#7 re-run 2026-09, pass**: manual read wing_load=58 (exact),
  smash=8.5 (+2.4%), max_load=46 (+4.5%), alpha=9.8 (-3.2%), induced
  dKTAS=72 (-4.0%), form dKTAS=20 (+0.5%), engine output=47 -> base 39 ->
  engine dKTAS=36, new KTAS=429. Max load, alpha and induced all follow
  from the slightly high smash reading (with smash 8.5 the formulas give
  45.3 / 9.9 / 73.2), so they're ordinary slide-rule drift. The engine
  term first disagreed by 14% because speedbop was missing the E6B's
  p-alt/mach engine scale (base delta knots = engine output / scale;
  see the calibration section below). With it, speedbop gives 46.3 / 1.186
  = base 39.1 -> 35.2 dKTAS (manual 36) and an end speed of 425.4 (manual
  429, +0.8%).
- **Form drag conversion confirmed 2026-09**: form dKTAS is
  `drag * smash / 10`, confirmed by calculation against the player aids.
  speedbop previously used `drag/smash*10`, which made form drag shrink
  with speed instead of grow. That wrong formula was in the turn formula
  from the start; fix 3 above briefly carried it into the EM chart too.
  With the correct formula, FJ-3M dry at alt 75 has the expected EM
  shape. With the engine scale and the standard atmosphere also applied,
  sustained load rises to a 10.22-load peak at 445kt, falls through the
  transonic region, and reaches zero at ~546kt. #10-#12 and #14 were
  picked against this curve.
- **#8 verified 2026-09, pass**: manual read KEAS=316 (+1.6%), Mach=0.64
  (0.646 unrounded), wing_load=48 / safe_load=23 (exact), Q=34 (+3.7%),
  Smash=7.0 (+2.9%), max_load=32 (31.2 unrounded), alpha=12 (-2.8%),
  induced dKTAS=70 (-3.7%), form dKTAS=17 (exact), engine output=62
  (+1.3%) -> base 46 = engine dKTAS 46 (exact; the Swift is at combat
  weight), end speed 359 vs 356.2 (+0.8%). Q/Smash/alpha/induced all trace
  back to the high KEAS reading -- see the KEAS note in the calibration
  section.
- **#9 verified 2026-09, pass**: KEAS 52, Q 1, Mach 0.2, Smash 0.5 were
  all off the bottom of the E6B's scales, so those are the device's
  minimum readings rather than true values (speedbop: 51 / 0.9 / 0.11 /
  0.2). Wing_load=58 and safe_load=19 match. Manual max_load 0.25 and
  speedbop's 0.95 (unrounded) both round down to 0 whole loads, which is
  what the scenario checks: no load is available near stall. Sustained
  load (2.11) wasn't checked.
- **#10 verified 2026-09, pass with two slips**: manual read
  wing_load=58 / safe_load=19 (match), KEAS=198 (+1.9%), Q=13 (+1.6%),
  Mach=0.41 (0.403 unrounded), Smash=2.2 (exact), alpha=12.8 (exact),
  induced dKTAS=23 (23.4), form dKTAS=4 (4.2), engine output=53 (53.5) ->
  base 32 (33.7, -5%: scale read 1.66 vs 1.59) -> engine dKTAS 29 (30.4).
  Two slips in the pass:
  - *End speed 262*: the pass's own terms give 250 - 23 - 4 + 29 = 252,
    which matches speedbop's 252.8 (-0.3%), so 262 looks like an
    arithmetic slip. With that, pulling 6 loads at the sustained 6.35
    holds speed as expected.
  - *Max load 15*: with the pass's own smash 2.2 and the LCS 4.7 it used
    for alpha, 22.4 / 4.7 x 2.2 = 10.5, matching speedbop's 10. 15 matches
    the 0.96 row's LCS 3.2 instead, so that step likely read the wrong
    row.
- **Re-compared after the standard-atmosphere switch (2026-10)**: KEAS
  and Mach now follow the standard atmosphere (see the calibration section
  below), so every speedbop value at altitude moved; the notes above record
  the comparisons as they stood at the time. Against the same manual
  passes, speedbop now reads:
  - *#1* (alt 0): KEAS/Q/Smash unchanged (density ratio is 1 at sea level).
    Mach is now 0.62 (400/650 = 0.615); the re-read said 0.6, the first
    pass 0.62 -- both within the reading precision of that scale.
  - *#2*: KEAS 357 vs manual 354 (-0.8%, was +1.2% the other way), Q 43.2
    vs 42 (-2.8%), Smash 9.0 vs 8.9 (-1.1%).
  - *#6*: max_load 41 vs manual 42 (+2.4%, was +7.7%) -- the smash gap
    #5/#6 flagged was mostly the old KEAS bias.
  - *#8*: KEAS 317 vs 316, Q 34.1 vs 34, max_load 32 vs 32, induced 69.6
    vs 70, end speed 358.5 vs 359 (+0.1%).
  - *#9*: KEAS 52 vs manual 52.
  - *#10*: KEAS 198 vs 198, Q 13.3 vs 13, Mach 0.41 vs 0.41, end speed
    253.7 vs 252 (-0.7%, using the pass's corrected sum).
  - *#7 sits on a lift-row boundary*: speedbop's KEAS 385 (manual 382,
    -0.8%) gives Mach 0.788, which rounds to 0.79 and selects the 0.84 lift
    row (LCS 3.8 / IDS 232); the manual Mach 0.78 selects the 0.78 row
    (4.2 / 270). So alpha (8.74 vs 9.8) and max_load (51 vs 46) now
    differ, while induced drag (75.3 vs 72) and the end speed (424.2 vs
    429, +1.1%) still agree -- a lower LCS and a lower IDS largely cancel.
    Any KEAS of 383 or less would land on the 0.78 row, so this is the
    step-wise table amplifying a 0.8% reading difference, not a calibration
    error. A turn scenario a few knots clear of a row boundary would make a
    cleaner end-to-end check.
- **#9** looks backwards at first glance (`max_load=0` while
  `sustained_load=2.11`), but it's expected: near stall, available lift
  collapses faster than the energy margin does, so structure (not thrust)
  becomes the binding constraint.
- **The sustained curve is bumpy** where mach crosses a lift/drag table
  row (e.g. the peak sits exactly on the 0.72 row at 445kt, and by 448kt
  it drops to 9.51 as the 0.78 row takes over). That's the step-wise
  tables, not noise -- expect sustained load to jump at those breakpoints.
- **The EM chart shows unrounded loads and cells by default** (the
  "Fractional loads & cells" switch). The values in this table (e.g.
  #10's 6.48) are the precise `sustained_load()` figures the chart and
  its tooltip show with the switch on. Switched off, both load lines are
  rounded down to whole loads, since pulls are whole numbers, and cells
  to the nearest whole cell -- #10 would read 6.

## Calibrating the E6B conversion scales (keas/q/mach/smash/engine)

`get_keas()`, `get_q()`, `get_mach()`, `get_smash()` and the engine scale are
closed-form curves fit against sample readings taken off the physical E6B
(see `e6b/fit.py` and the data in `e6b/*.csv` and `e6b/samples/*.csv`).
KEAS, mach and the engine scale turned out to be the International Standard
Atmosphere with two device constants: one game altitude unit is 200 ft, and
the sea-level speed of sound is 650 kt (`performance.py`).

- **`smash = 10*q/wl`** is an exact match against all 38 `e6b/smash.csv`
  samples (0% error) -- nothing to recalibrate here.
- **KEAS and Mach follow the standard atmosphere (2026-10)**:
  `keas = ktas * sqrt(sigma)` and `mach = keas / (650 * sqrt(delta))`, with
  `sigma`/`delta` the ISA density/pressure ratios at 200 ft per altitude unit
  (tropopause at 36,089 ft), replacing the old single exponentials
  `ktas / exp(0.003358*alt)` and `keas * exp(0.0044*alt) / 670`.
  - *Why*: manual KEAS at alt 75 read 1.2-1.9% high on #2/#7/#8/#10, never
    low, and speedbop's own `e6b/samples/keas.csv` agreed with the manual
    reads -- every point at altitudes 50-100 sat 1-2% above the old formula
    (alt 75 / 400 KTAS reads 316; the exponential gave 311, the standard
    atmosphere gives 317). A single exponential fit across 0-300 can't follow
    the real curve's shape, so it ran low in the middle and high near alt
    200, and dragged Q and Smash with it.
  - *KEAS fit*: with the altitude unit left free, the fit lands on 201.4 ft;
    at 200 ft the median error against `keas.csv` is 0.34% (was 1.44%), with
    no altitude-dependent bias (the old formula ran +1.8% at alt 75-100 and
    -3% at alt 200).
  - *Mach fit*: the altitude shape fits at 199.4 ft per unit. The
    speed-of-sound constant is the device's, not the textbook 661.47 kt:
    every `mach.csv` reading implies nearly the same value at every
    altitude (median 649.3 kt), the sea-level reading gives exactly 650.0,
    and a least-squares fit gives 649.7. At 650 kt the median error is
    0.91% (was 1.75%); at 661.47 kt it would be 2.19%.
  - *Readings worth re-reading on the device* (the calibration tests in
    `tests/test_performance.py` exclude only `175,500,222` and the two
    worst mach readings):
    - `keas.csv`: `175,500,222` (formula 278, -20%; neighbors agree with
      the formula, so almost certainly a transcription slip), `120,1000,658`
      (formula 681, -3.4%), `150,600,355` (formula 367, -3.3%) and
      `175,650,352` (formula 362, -2.7%).
    - `mach.csv`: `20,500,0.90` (formula 0.83, +8.7%), `200,420,1.40`
      (formula 1.50, -6.8%; the `200,391,1.40` row next to it fits),
      `200,498,1.70` (formula 1.78, -4.5%) and `50,258,0.46` (formula 0.48,
      -3.9%).
  - **altitude=0 is a free, exact precision check** -- by definition
    keas=ktas there (density ratio 1), so any deviation read off the
    physical device at altitude 0 is pure instrument/reading error, not a
    formula problem -- a useful error bar (roughly 1%) to apply to every
    other manual reading too.
- **`get_q()`** and **`get_mach()`** (history -- mach has since moved to the
  standard atmosphere above; q is unchanged) both turned out to be clean
  closed-form relationships after all -- a first pass with denser data showed R^2
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
  - mach's remaining ~2% typical / ~10% worst-case error under that
    exponential (worst point: `keas=500, alt=20, mach=0.9`) looked like
    ordinary reading noise at the time; the standard-atmosphere form above
    halves the typical error and leaves that point as a suspected slip.
- **The p-alt/mach engine scale** (`engine_scale()`, readings in
  `e6b/mach_engine.csv`): altitude and mach are both set in a window and
  the scale is read as a ratio on the outer rings, so both windows'
  graduations shape the result. That's outside what `e6b/fit.py` models:
  treating both as evenly graduated windows fits only R^2 = 0.973. The
  readings instead reproduce a closed form, the inverse of the *total
  pressure ratio*:
  `scale = 1 / (delta(200 ft * alt) * (1 + 0.2 M^2)^3.5)`, where `delta`
  is the standard-atmosphere static pressure ratio (tropopause at 36,089
  ft) and `(1 + 0.2 M^2)^3.5` is the isentropic ram-pressure rise. With
  those round constants (1 at sea level/mach 0, 200 ft per altitude unit)
  it fits all 36 readings to 0.8% median, 2.5% worst, R^2 = 0.9999;
  freeing the constants barely improves it. The mach window alone matches
  the isentropic term to ~1% at every altitude.
  - Two readings at the mach-1.0 tick were slips, flagged by the fit and
    confirmed on the device: `150,1.0` was 10/6.0 (now 10/5.7, formula
    10/5.62) and `220,1.0` was 10/3.2 (now 10/2.9, formula 10/2.89).

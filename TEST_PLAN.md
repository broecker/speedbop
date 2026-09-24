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
| 1 | KEAS/Q/Smash/Mach at sea level (KEAS=KTAS) | FJ-3M, 15.7t, SL, 400 KTAS | KEAS=400, Q=54.2, Smash=10.4, Mach=0.6 | Slide rule |
| 2 | Same, at altitude (KEAS != KTAS) | Swift Mk5, 15.8t, 7,500ft, 450 KTAS | KEAS=350, Q=41.5, Smash=8.7, Mach=0.7 | Slide rule |
| 3 | LCS/IDS/form-drag table lookup | FJ-3M @ mach 0.6 | LCS=4.7, IDS=328, form drag=19 | ADC printed tables |
| 4 | Engine output isobar interpolation | Swift Mk5 @ mach 0.7, 7,500ft | dry=40.9, AB=60.1 | ADC engine chart |
| 5 | Structural/lift-limited max load | FJ-3M, case 1 (400kt/SL) | max_load=49 (16.3G) | ADC alpha_max + slide rule |
| 6 | Same, different aircraft/altitude | Swift Mk5, case 2 (450kt/7,500ft) | max_load=39 (13G) | ADC alpha_max + slide rule |
| 7 | Full turn resolution, dry | FJ-3M, 17.4t, 7,500ft, 485kt, 20 pulls, dAlt=0 | (run and record new KTAS) | Manual slide-rule turn |
| 8 | Full turn resolution, AB | Swift Mk5, 15.8t, 7,500ft, 400kt, 15 pulls, dAlt=0, AB on | (run and record new KTAS) | Manual slide-rule turn, AB engine table |
| 9 | Near-stall regime: lift collapses faster than energy margin | FJ-3M, 17.4t, 7,500ft, 65kt | max_load=0, sustained_load=2.51 | Slide rule (confirm no G available near stall regardless of thrust) |
| 10 | Sustained load, rising side | FJ-3M dry, 17.4t, 7,500ft, 250kt | sustained_load=7.28 | Pull 7 loads for one turn, expect approximately 250kt after |
| 11 | Sustained load, at the peak | FJ-3M dry, 17.4t, 7,500ft, 465kt | sustained_load=12.47 (curve's max) | Pull 12 loads, expect approximately 465kt after |
| 12 | Sustained load, declining/transonic side | FJ-3M dry, 17.4t, 7,500ft, 550kt | sustained_load=6.86 (well below the peak) | Pull 7 loads, expect approximately 550kt after -- confirms the peak-and-decline is real, not a bug |
| 13 | Literature corner speed | FJ-3M, combat_safe_load=21 | 246 KEAS (approximately 316 KTAS @ 7,500ft) | ADC's stated G rating + slide rule solve |
| 14 | Sustained x structural crossing ("Sustained" marker) | FJ-3M dry, 17.4t, 7,500ft | approximately 188 KTAS / 5.65 loads | Confirm sustained_load approximately equals max_load independently at that speed |
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

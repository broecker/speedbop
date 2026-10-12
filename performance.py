"""Aircraft performance: the E6B's engine and airspeed conversions, and the
sustained-turn (energy-maneuverability) analysis behind the Aircraft
Performance chart.

Every data class and the turn-resolution mechanics live in speedbop. The two
modules import each other as whole modules (speedbop needs engine_scale()
for each turn; this module builds speedbop's AircraftState and result
classes) and only look names up at call time, so the import cycle is safe.
"""

from __future__ import annotations

import math

import speedbop


FEET_PER_ALTITUDE_UNIT = 200.0


def pressure_ratio(altitude: float) -> float:
    """Standard-atmosphere static pressure ratio p/p0 (tropopause at 36,089 ft)."""
    feet = altitude * FEET_PER_ALTITUDE_UNIT
    if feet <= 36089.0:
        return (1 - 6.87559e-6 * feet) ** 5.25588
    return 0.22336 * math.exp(-4.80634e-5 * (feet - 36089.0))


def engine_scale(altitude: float, mach: float) -> float:
    """The E6B's p-alt/mach engine scale: base delta knots = engine output / scale.

    Fit against e6b/mach_engine.csv, the scale is the inverse of the total
    pressure ratio: static pressure at altitude times the isentropic ram
    rise (1 + 0.2 M^2)^3.5, with one altitude unit = 200 ft.
    """
    return 1.0 / (pressure_ratio(altitude) * (1 + 0.2 * mach**2) ** 3.5)


# Inverse helpers -- going from a derived value back to the inputs that
# would have produced it, e.g. when a known smash/q/mach reading needs to
# be converted back into ktas for the next turn's calculation.


def q_from_smash(smash: float, wl: float) -> float:
    """Inverse of AircraftState.get_smash(): smash = 10 * q / wl."""
    return smash * wl / 10.0


def keas_from_q(q: float) -> float:
    """Inverse of AircraftState.get_q(): q = keas^2 / 2950."""
    return math.sqrt(q * 2950)


def ktas_from_keas(keas: float, altitude: float) -> float:
    """Inverse of AircraftState.get_keas(): keas = ktas / exp(0.003358*altitude)."""
    return keas * math.exp(0.003358 * altitude)


def ktas_from_q(q: float, altitude: float) -> float:
    """Chains keas_from_q and ktas_from_keas."""
    return ktas_from_keas(keas_from_q(q), altitude)


def corner_speed(state: speedbop.AircraftState) -> int:
    """Literature corner speed in KEAS: where the lift-limited load reaches
    combat_safe_load, using this state's LCS and wing loading."""
    alpha_over_lcs = state.adc.lift.alpha_max / state.get_lcs()
    desired_smash = state.adc.characteristics.combat_safe_load / alpha_over_lcs
    desired_q = q_from_smash(desired_smash, state.get_wing_load())
    desired_keas = keas_from_q(desired_q)
    return math.floor(desired_keas)


def sustained_load(state: speedbop.AircraftState, afterburner: bool = True) -> float:
    """Max load (same units as get_max_load()) sustainable indefinitely at
    this exact state -- the load at which induced + form drag exactly
    balances engine thrust, so KTAS neither rises nor falls turn over turn.
    Above this (up to get_max_load()'s structural ceiling), every turn
    bleeds energy; below it, every turn gains energy.

    Closed-form, not iterative: in calculate_performance(), with no
    segment_fp override, induced_delta_ktas reduces to load**2 * k for a k
    that doesn't depend on load, and neither form_delta_ktas nor
    engine_delta_ktas depend on load at all -- so solving for the load
    where the net change is zero is just inverting that square. Unlike
    get_max_load(), this is an analysis value, not a live per-turn clamp,
    so it's left as a precise float (not floored to an int) and NOT capped
    at get_max_load() -- callers wanting the actually achievable sustained
    G take min(sustained_load(), get_max_load()) themselves.
    """
    smash = state.get_smash()
    if smash == 0:
        # No meaningful lift at this speed (q/smash -> 0 as ktas -> 0) --
        # there's no load, however small, it can sustain.
        return 0.0
    net_delta_ktas = (
        state.get_engine_delta_ktas(state.get_engine_output(afterburner))
        - state.get_form_delta_ktas()
    )
    if net_delta_ktas <= 0:
        return 0.0
    k = 100 * state.get_lcs() / (smash * state.get_ids())
    return math.sqrt(net_delta_ktas / k)


def phad_cells_from_load(load: float, fp: int) -> float:
    """Rule-of-thumb PHAD-cell turn rate: every half-FP's worth of load
    spent turns one cell -- e.g. 8 load at 8 FP turns 2 cells."""
    return 2 * load / fp


def round_phad_cells(cells: float) -> int:
    """Whole PHAD cells, rounded to the nearest cell (halves round up)."""
    return math.floor(cells + 0.5)


def sustained_turn_profile(
    adc: speedbop.AircraftDataCard,
    weight: float,
    altitude: int,
    ktas_values: list[float],
    afterburner: bool = True,
) -> list[speedbop.SustainedTurnPoint]:
    """Sustained-turn performance across a range of speeds at one altitude --
    the data behind an energy-maneuverability chart: for each speed, how
    many Gs can this aircraft sustain indefinitely (sustained_load) versus
    how many Gs the airframe can take at all (max_load), and the PHAD-cell
    turn rate each implies. See find_best_sustained_turn() for where the
    sustained_load/max_load curves cross.

    The speed range is entirely up to the caller (a chart, a script, a
    test) -- this just builds one AircraftState per KTAS value and reads
    its performance off it, the same way any other turn-by-turn state would.
    """
    points = []
    for ktas in ktas_values:
        state = speedbop.AircraftState(adc, weight, ktas, altitude)
        speed_fp = speedbop.speed_fp_from_ktas(ktas)
        whole_sustained_load = math.floor(sustained_load(state, afterburner))
        max_load = state.get_max_load()
        sustained_phad_cells = phad_cells_from_load(whole_sustained_load, speed_fp)
        max_phad_cells = phad_cells_from_load(max_load, speed_fp)
        points.append(
            speedbop.SustainedTurnPoint(
                ktas=ktas,
                mach=state.get_mach(),
                speed_fp=speed_fp,
                sustained_load=whole_sustained_load,
                max_load=max_load,
                sustained_phad_cells=sustained_phad_cells,
                max_phad_cells=max_phad_cells,
                # Whichever of the two turn rates is actually achievable at
                # this speed, as a whole cell count.
                max_pullable_cells=round_phad_cells(max(sustained_phad_cells, max_phad_cells)),
                engine_output=state.get_engine_output(afterburner),
                total_drag=state.get_total_drag(),
            )
        )
    return points


def find_best_sustained_turn(
    points: list[speedbop.SustainedTurnPoint],
) -> speedbop.BestSustainedTurn | None:
    """Linearly interpolates between the two profile points straddling
    where sustained_load and max_load cross, walking the list in the order
    given (matching sustained_turn_profile()'s ascending-KTAS sweep) and
    returning the first crossing found. Returns None if the two curves
    never cross across the swept range -- one dominates the other
    throughout, so there's no single best point to highlight.

    Points with max_load <= 0 (near-zero airspeed, where get_smash() and
    get_max_load() both bottom out at 0) are skipped first -- otherwise a
    profile starting at ktas=0 finds a trivial "crossing" right at the
    start, where both curves are pinned at zero because the aircraft can't
    generate any lift at all yet, not because that's a meaningful sustained
    turn point.

    Equal loads count as sustained still reaching max_load, not as a
    crossing: with both curves in whole loads they often touch at low speed
    and run level together, and the crossing that matters is where
    sustained finally drops below max_load.
    """
    flying = [p for p in points if p.max_load > 0]

    for prev, curr in zip(flying, flying[1:]):
        prev_diff = prev.sustained_load - prev.max_load
        curr_diff = curr.sustained_load - curr.max_load

        if (prev_diff < 0) != (curr_diff < 0):
            t = prev_diff / (prev_diff - curr_diff)
            ktas = prev.ktas + t * (curr.ktas - prev.ktas)
            load = prev.sustained_load + t * (curr.sustained_load - prev.sustained_load)
            # speed_fp is a step function of KTAS -- prev's FP is a fine
            # approximation given how narrow the bracket between two
            # adjacent profile samples usually is.
            return speedbop.BestSustainedTurn(
                ktas=ktas,
                load=load,
                phad_cells=phad_cells_from_load(load, prev.speed_fp),
            )
    return None


def find_structural_corner_speed(
    points: list[speedbop.SustainedTurnPoint], combat_safe_load: float
) -> speedbop.StructuralCornerPoint | None:
    """Linearly interpolates between the two profile points straddling
    where max_load first reaches combat_safe_load, walking the list in the
    order given (matching sustained_turn_profile()'s ascending-KTAS sweep).

    If max_load already meets combat_safe_load at the very first point (the
    true crossing is below the swept range), that point is returned as-is
    rather than extrapolating. Returns None if max_load never reaches
    combat_safe_load across the swept range.
    """
    for prev, curr in zip(points, points[1:]):
        if prev.max_load >= combat_safe_load:
            return speedbop.StructuralCornerPoint(ktas=prev.ktas, load=combat_safe_load)
        if curr.max_load >= combat_safe_load:
            t = (combat_safe_load - prev.max_load) / (curr.max_load - prev.max_load)
            ktas = prev.ktas + t * (curr.ktas - prev.ktas)
            return speedbop.StructuralCornerPoint(ktas=ktas, load=combat_safe_load)
    return None

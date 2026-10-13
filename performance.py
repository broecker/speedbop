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


# The E6B's airspeed and engine windows follow the International Standard
# Atmosphere with these two device constants, fit against the readings in
# e6b/ (see TEST_PLAN.md).
FEET_PER_ALTITUDE_UNIT = 200.0
SPEED_OF_SOUND_KT = 650.0  # at sea level

TROPOPAUSE_FT = 36089.0
_LAPSE_PER_FT = 6.87559e-6
_DECAY_PER_FT = 4.80634e-5  # above the tropopause
_TROPOPAUSE_PRESSURE_RATIO = (1 - _LAPSE_PER_FT * TROPOPAUSE_FT) ** 5.25588

# Level-turn rate (see turn_degrees()): one game turn, the PHAD's heading
# resolution, and the physics to get from a load to a heading change.
GAME_TURN_SECONDS = 6.0
PHAD_CELL_DEGREES = 30.0
LOADS_PER_G = 3.0  # as in speedbop.gs_from_pulls()
STANDARD_GRAVITY_FT_S2 = 32.174
FT_PER_S_PER_KNOT = 1.68781


def temperature_ratio(altitude: float) -> float:
    """Standard-atmosphere temperature ratio T/T0, constant above the tropopause."""
    feet = altitude * FEET_PER_ALTITUDE_UNIT
    return 1 - _LAPSE_PER_FT * min(feet, TROPOPAUSE_FT)


def pressure_ratio(altitude: float) -> float:
    """Standard-atmosphere static pressure ratio p/p0."""
    feet = altitude * FEET_PER_ALTITUDE_UNIT
    if feet <= TROPOPAUSE_FT:
        return temperature_ratio(altitude) ** 5.25588
    return _TROPOPAUSE_PRESSURE_RATIO * math.exp(-_DECAY_PER_FT * (feet - TROPOPAUSE_FT))


def density_ratio(altitude: float) -> float:
    """Standard-atmosphere density ratio rho/rho0."""
    return pressure_ratio(altitude) / temperature_ratio(altitude)


def keas_from_ktas(ktas: float, altitude: float) -> float:
    """Equivalent airspeed: true airspeed scaled by sqrt(density ratio)."""
    return ktas * math.sqrt(density_ratio(altitude))


def mach_from_keas(keas: float, altitude: float) -> float:
    """Mach from equivalent airspeed and pressure altitude, the way the E6B's
    window reads it: keas / (a0 * sqrt(pressure ratio))."""
    return keas / (SPEED_OF_SOUND_KT * math.sqrt(pressure_ratio(altitude)))


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
    """Inverse of keas_from_ktas()."""
    return keas / math.sqrt(density_ratio(altitude))


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
    return _load_spending(state, _thrust_minus_form_delta_ktas(state, afterburner))


def departure_load(state: speedbop.AircraftState, afterburner: bool = True) -> float:
    """The load at which one full level turn ends at exactly 0 KTAS: it
    spends the aircraft's whole speed plus whatever thrust adds back. Any
    pull at or above it departs flight. The same closed form as
    sustained_load(), unrounded and not capped at get_max_load().
    """
    return _load_spending(state, state.ktas + _thrust_minus_form_delta_ktas(state, afterburner))


def _thrust_minus_form_delta_ktas(state: speedbop.AircraftState, afterburner: bool) -> float:
    """The part of a turn's speed change that doesn't depend on load."""
    return (
        state.get_engine_delta_ktas(state.get_engine_output(afterburner))
        - state.get_form_delta_ktas()
    )


def _load_spending(state: speedbop.AircraftState, delta_ktas: float) -> float:
    """The full-turn load whose induced drag costs exactly delta_ktas."""
    smash = state.get_smash()
    if smash == 0 or delta_ktas <= 0:
        # No meaningful lift at this speed (q/smash -> 0 as ktas -> 0), or
        # nothing to spend -- no load, however small, fits.
        return 0.0
    k = 100 * state.get_lcs() / (smash * state.get_ids())
    return math.sqrt(delta_ktas / k)


def turn_degrees(load: float, ktas: float) -> float:
    """Heading change over one game turn of a level turn at this load.

    In a level turn 1 G of the load holds the aircraft up, so only the
    horizontal part, sqrt(G**2 - 1), turns it: turn rate = g * sqrt(G**2 - 1)
    / true airspeed. A load of 1 G (3 loads) or less doesn't turn at all.
    """
    gs = load / LOADS_PER_G
    if gs <= 1 or ktas <= 0:
        return 0.0
    turn_rate = STANDARD_GRAVITY_FT_S2 * math.sqrt(gs**2 - 1) / (ktas * FT_PER_S_PER_KNOT)
    return math.degrees(turn_rate * GAME_TURN_SECONDS)


def phad_cells_from_load(load: float, ktas: float) -> float:
    """Level-turn PHAD cells for one game turn at this load, unrounded."""
    return turn_degrees(load, ktas) / PHAD_CELL_DEGREES


def whole_phad_cells(cells: float) -> int:
    """Whole PHAD cells turned in one game turn: rounded down, since
    leftover degrees don't carry over to the next turn."""
    # The epsilon keeps float noise (2.9999999999) from costing a cell.
    return math.floor(cells + 1e-9)


def sustained_turn_profile(
    adc: speedbop.AircraftDataCard,
    weight: float,
    altitude: int,
    ktas_values: list[float],
    afterburner: bool = True,
    fractional: bool = True,
) -> list[speedbop.SustainedTurnPoint]:
    """Sustained-turn performance across a range of speeds at one altitude --
    the data behind an energy-maneuverability chart: for each speed, how
    many Gs can this aircraft sustain indefinitely (sustained_load) versus
    how many Gs the airframe can take at all (max_load), and the PHAD-cell
    turn rate each implies. See find_best_sustained_turn() for where the
    sustained_load/max_load curves cross, and find_best_sustained_turn_rate()
    for the speeds that hold the most cells.

    The speed range is entirely up to the caller (a chart, a script, a
    test) -- this just builds one AircraftState per KTAS value and reads
    its performance off it, the same way any other turn-by-turn state would.

    Loads and cells are left unrounded by default. fractional=False clamps
    them the way play does: whole loads, and whole cells for the two
    turn-rate fields, both rounded down.
    """
    def turn_cells(load: float, ktas: float) -> float:
        cells = phad_cells_from_load(load, ktas)
        return cells if fractional else whole_phad_cells(cells)

    points = []
    for ktas in ktas_values:
        state = speedbop.AircraftState(adc, weight, ktas, altitude)
        speed_fp = speedbop.speed_fp_from_ktas(ktas)
        departure = departure_load(state, afterburner)
        if fractional:
            sustained = sustained_load(state, afterburner)
            max_load = state.get_lift_limited_load()
            pullable = min(max_load, departure)
        else:
            sustained = math.floor(sustained_load(state, afterburner))
            max_load = state.get_max_load()
            # The most whole loads strictly below the departure load.
            pullable = min(max_load, max(0, math.ceil(departure) - 1))
        points.append(
            speedbop.SustainedTurnPoint(
                ktas=ktas,
                mach=state.get_mach(),
                speed_fp=speed_fp,
                sustained_load=sustained,
                max_load=max_load,
                sustained_phad_cells=phad_cells_from_load(sustained, ktas),
                max_phad_cells=phad_cells_from_load(max_load, ktas),
                # The most a turn can pull is max_load, sustainable or not
                # -- spare energy beyond it can't buy a tighter turn -- unless
                # pulling that much would depart flight.
                max_pullable_load=pullable,
                max_pullable_cells=turn_cells(pullable, ktas),
                sustained_turn_cells=turn_cells(min(sustained, max_load), ktas),
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
    returning the first crossing found. This is where max-load turns settle,
    not necessarily the best sustained turn rate -- see
    find_best_sustained_turn_rate() for that. Returns None if the two curves
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
            return speedbop.BestSustainedTurn(
                ktas=ktas,
                load=load,
                phad_cells=phad_cells_from_load(load, ktas),
            )
    return None


def find_best_sustained_turn_rate(
    points: list[speedbop.SustainedTurnPoint],
) -> speedbop.BestSustainedTurnRate | None:
    """The point with the highest sustained_turn_cells, taking the slowest
    of any that tie -- in whole numbers many speeds tie on whole cells. It's
    only as fine as the sweep's sample spacing.

    Returns None if no point can hold a turn at all.
    """
    best = max(points, key=lambda p: (p.sustained_turn_cells, -p.ktas), default=None)
    if best is None or best.sustained_turn_cells <= 0:
        return None
    return speedbop.BestSustainedTurnRate(
        cells=best.sustained_turn_cells,
        ktas=best.ktas,
        load=min(best.sustained_load, best.max_load),
    )


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

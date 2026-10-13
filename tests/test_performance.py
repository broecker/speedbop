import math
import statistics

import pytest

import speedbop
from e6b.fit import load_samples
from factories import REAL_ADC_PATH, REPO_ROOT, make_ab_adc, make_adc, make_state
from performance import (
    FEET_PER_ALTITUDE_UNIT,
    GAME_TURN_SECONDS,
    TROPOPAUSE_FT,
    corner_speed,
    density_ratio,
    departure_load,
    engine_scale,
    find_best_sustained_turn,
    find_best_sustained_turn_rate,
    find_structural_corner_speed,
    keas_from_q,
    ktas_from_keas,
    ktas_from_q,
    keas_from_ktas,
    mach_from_keas,
    phad_cells_from_load,
    pressure_ratio,
    q_from_smash,
    sustained_load,
    sustained_turn_profile,
    turn_degrees,
    whole_phad_cells,
)
from speedbop import (
    AircraftDataCard,
    AircraftState,
    BestSustainedTurn,
    BestSustainedTurnRate,
    StructuralCornerPoint,
    SustainedTurnPoint,
    calculate_performance,
)


# ---------------------------------------------------------------------------
# sustained_load
# ---------------------------------------------------------------------------

def test_sustained_load_is_positive_and_below_the_structural_max():
    state = make_state(adc=make_ab_adc(), ktas=325.0, altitude=0)

    sustained = sustained_load(state)

    assert sustained > 0
    assert sustained < state.get_max_load()


def test_sustained_load_zero_crossing_matches_calculate_performance():
    # Cross-checks the closed-form formula against the real per-turn
    # formula in calculate_performance() -- if that formula ever changes,
    # this test catches the drift instead of the two silently diverging.
    # segment_pulls is typed as int, but nothing here needs it to actually
    # be one -- the sustained load is generally fractional, and the whole
    # point is finding the exact zero crossing.
    state = make_state(adc=make_ab_adc(), ktas=325.0, altitude=0)
    sustained = sustained_load(state)

    performance = calculate_performance(state, segment_pulls=sustained, delta_altitude=0)

    assert performance.new_state.ktas == pytest.approx(state.ktas, abs=0.01)


def test_sustained_load_returns_zero_when_drag_exceeds_available_thrust():
    # The aircraft is already decelerating in straight, level flight at
    # this speed/altitude -- there's no load, however small, it can sustain.
    huge_drag_form = AircraftDataCard.Form(brake=0, mach_to_drag_table={0.5: 999.0})
    state = make_state(adc=make_adc(form=huge_drag_form), ktas=325.0, altitude=0)

    assert sustained_load(state) == 0.0


def test_sustained_load_returns_zero_at_zero_speed_instead_of_dividing_by_zero():
    # Regression test: get_smash() legitimately returns 0 as ktas -> 0
    # (q/wing_load both go to 0), and the formula's k divides BY smash --
    # this used to raise ZeroDivisionError rather than just reporting "no
    # sustainable load at this speed."
    state = make_state(adc=make_ab_adc(), ktas=0.0, altitude=0)

    assert state.get_smash() == 0.0  # confirms this test actually hits the case
    assert sustained_load(state) == 0.0


def test_sustained_load_matches_calculate_performance_with_form_drag_and_weight():
    # Regression test: the synthetic fixtures have zero form drag, which hid
    # get_sustained_load() subtracting raw drag where calculate_performance()
    # subtracts the converted form dKTAS. The real FJ-3M off combat weight
    # exercises both the form-drag conversion and the engine weight scaling.
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)
    state = make_state(adc=adc, weight=17.4, ktas=465.0, altitude=75)
    assert state.get_total_drag() > 0
    assert state.weight != adc.stores.combat_weight

    sustained = sustained_load(state, afterburner=False)
    assert 0 < sustained < state.get_max_load()

    performance = calculate_performance(
        state, segment_pulls=sustained, delta_altitude=0, afterburner=False
    )

    assert performance.new_state.ktas == pytest.approx(state.ktas, abs=0.01)


def test_sustained_load_afterburner_increases_it():
    state = make_state(adc=make_ab_adc(), ktas=325.0, altitude=0)

    dry = sustained_load(state, afterburner=False)
    ab = sustained_load(state, afterburner=True)

    assert ab > dry


# ---------------------------------------------------------------------------
# departure_load
# ---------------------------------------------------------------------------

def test_departure_load_ends_the_turn_at_zero_ktas():
    # Cross-checks the closed form against calculate_performance(), like
    # the sustained_load test above. 550kt is fast enough that the wing
    # could pull more (71 loads) than the speed can pay for.
    state = AircraftState(AircraftDataCard.from_json(REAL_ADC_PATH), weight=17.4, ktas=550.0, altitude=75)
    load = departure_load(state, afterburner=False)
    assert load < state.get_max_load()

    turn = calculate_performance(state, segment_pulls=load, afterburner=False)

    assert turn.new_state.ktas == pytest.approx(0.0, abs=0.01)


def test_departure_load_is_above_sustained_load():
    # A sustained turn keeps its speed, so it's always well short of
    # spending all of it.
    state = make_state(adc=make_ab_adc(), ktas=325.0, altitude=0)

    assert departure_load(state) > sustained_load(state) > 0


# ---------------------------------------------------------------------------
# PHAD cells / sustained_turn_profile
# ---------------------------------------------------------------------------

def test_turn_degrees_matches_the_pilots_rate_of_turn_rule():
    # Rate of turn in deg/s = 1091 x tan(bank) / KTAS, and in a level turn
    # tan(bank) = sqrt(G**2 - 1). 6 loads is 2 G: a 60 degree bank.
    deg_per_second = 1091 * math.tan(math.radians(60)) / 300
    assert turn_degrees(load=6.0, ktas=300.0) == pytest.approx(
        deg_per_second * GAME_TURN_SECONDS, rel=0.002
    )


def test_turn_degrees_needs_more_than_one_g():
    # 3 loads (1 G) only holds a level turn's altitude.
    assert turn_degrees(load=3.0, ktas=300.0) == 0.0
    assert turn_degrees(load=2.0, ktas=300.0) == 0.0
    assert turn_degrees(load=0.0, ktas=300.0) == 0.0


def test_turn_degrees_fall_inversely_with_true_airspeed():
    assert turn_degrees(load=9.0, ktas=400.0) == pytest.approx(turn_degrees(load=9.0, ktas=200.0) / 2)


def test_phad_cells_from_load_counts_30_degree_cells():
    assert phad_cells_from_load(load=9.0, ktas=300.0) == pytest.approx(turn_degrees(9.0, 300.0) / 30)


def test_phad_cells_from_load_has_no_step_at_fp_boundaries():
    # Regression test: the old 2 x load / FP rule of thumb lost a seventh of
    # the turn rate at the 6 -> 7 FP step at 260kt. True airspeed has no step.
    assert speedbop.speed_fp_from_ktas(259.9) == 6
    assert speedbop.speed_fp_from_ktas(260.0) == 7
    assert phad_cells_from_load(10.0, 259.9) == pytest.approx(phad_cells_from_load(10.0, 260.0), rel=1e-3)


def test_whole_phad_cells_rounds_down():
    # Leftover degrees don't carry over to the next turn.
    assert whole_phad_cells(2.0) == 2
    assert whole_phad_cells(2.86) == 2
    assert whole_phad_cells(2.99) == 2
    # Float noise doesn't cost a cell.
    assert whole_phad_cells(3.0 - 1e-12) == 3


def test_sustained_turn_profile_returns_one_point_per_ktas_value():
    adc = make_ab_adc()
    ktas_values = [100.0, 200.0, 300.0]

    points = sustained_turn_profile(adc, weight=17.4, altitude=0, ktas_values=ktas_values)

    assert [p.ktas for p in points] == ktas_values
    assert all(isinstance(p, SustainedTurnPoint) for p in points)


def test_sustained_turn_profile_is_unrounded_by_default():
    # alpha_max 21 so the lift-limited load isn't a whole number here.
    adc = make_ab_adc(
        lift=AircraftDataCard.Lift(alpha_max=21.0, mach_lcs_ids_table={0.5: (4.0, 100), 1.0: (2.0, 50)})
    )
    state = AircraftState(adc, weight=17.4, ktas=337.3, altitude=75)

    (point,) = sustained_turn_profile(adc, weight=17.4, altitude=75, ktas_values=[337.3])

    expected_sustained = sustained_load(state)
    expected_max = state.get_lift_limited_load()
    # Confirms the fixture actually has fractions to lose.
    assert expected_sustained != math.floor(expected_sustained)
    assert expected_max != math.floor(expected_max)

    assert point.sustained_load == pytest.approx(expected_sustained)
    assert point.max_load == pytest.approx(expected_max)
    expected_pullable = min(expected_max, departure_load(state))
    assert point.max_pullable_load == pytest.approx(expected_pullable)
    assert point.max_pullable_cells == pytest.approx(phad_cells_from_load(expected_pullable, state.ktas))
    assert point.sustained_turn_cells == pytest.approx(
        phad_cells_from_load(min(expected_sustained, expected_max), state.ktas)
    )


def test_sustained_turn_profile_in_whole_numbers_matches_calling_aircraft_state_directly():
    adc = make_ab_adc()
    # Nonzero altitude so get_keas() != ktas -- catches speed_fp or the turn
    # rate being computed from the wrong one (see the regression test below).
    state = AircraftState(adc, weight=17.4, ktas=337.3, altitude=75)

    (point,) = sustained_turn_profile(
        adc, weight=17.4, altitude=75, ktas_values=[337.3], fractional=False
    )

    expected_speed_fp = speedbop.speed_fp_from_ktas(state.ktas)
    # Whole loads, rounded down, like get_max_load().
    expected_sustained = math.floor(sustained_load(state))
    assert point.mach == pytest.approx(state.get_mach())
    assert point.speed_fp == expected_speed_fp
    assert point.sustained_load == expected_sustained
    assert point.max_load == state.get_max_load()
    assert point.sustained_phad_cells == pytest.approx(
        phad_cells_from_load(expected_sustained, state.ktas)
    )
    assert point.max_phad_cells == pytest.approx(
        phad_cells_from_load(state.get_max_load(), state.ktas)
    )
    # Whole loads strictly below the departure load.
    expected_pullable = min(state.get_max_load(), math.ceil(departure_load(state)) - 1)
    assert point.max_pullable_load == expected_pullable
    assert point.max_pullable_cells == whole_phad_cells(
        phad_cells_from_load(expected_pullable, state.ktas)
    )
    assert point.sustained_turn_cells == whole_phad_cells(
        phad_cells_from_load(min(expected_sustained, state.get_max_load()), state.ktas)
    )
    assert point.engine_output == pytest.approx(state.get_engine_output())
    assert point.total_drag == pytest.approx(state.get_total_drag())


def test_sustained_turn_profile_max_pullable_cells_tracks_max_load():
    # Regression test: max_pullable_cells used to take the higher of the
    # sustained and lift-limited rates, so at low speed -- where
    # sustained_load (energy) exceeds max_load (lift) -- it claimed a turn
    # rate from a load the wing can't pull. The turn rate is capped by
    # max_load on both sides of the crossing. A combat weight well above the
    # flying weight scales engine output up, so the slow point has energy to
    # spare.
    adc = make_ab_adc(stores=AircraftDataCard.Stores(combat_weight=40.0))
    (low_speed_point,) = sustained_turn_profile(
        adc, weight=17.4, altitude=0, ktas_values=[130.0], fractional=False
    )
    (high_speed_point,) = sustained_turn_profile(
        adc, weight=17.4, altitude=0, ktas_values=[300.0], fractional=False
    )

    # Confirms this test covers both sides of the crossing.
    assert low_speed_point.sustained_phad_cells > low_speed_point.max_phad_cells
    assert high_speed_point.sustained_phad_cells < high_speed_point.max_phad_cells

    # 5 sustainable loads at 130kt would turn 2 cells, but only 3 loads --
    # 1 G, no turn at all -- can be pulled.
    assert (low_speed_point.sustained_load, low_speed_point.max_load) == (5, 3)
    assert whole_phad_cells(low_speed_point.sustained_phad_cells) == 2
    assert low_speed_point.max_pullable_cells == 0
    assert high_speed_point.max_pullable_cells == whole_phad_cells(high_speed_point.max_phad_cells)


def test_sustained_turn_cells_takes_the_lower_curve():
    # Holding a turn needs a load that's both sustainable and pullable:
    # lift-limited at low speed, energy-limited at high speed.
    adc = make_ab_adc(stores=AircraftDataCard.Stores(combat_weight=40.0))
    (low_speed_point,) = sustained_turn_profile(adc, weight=17.4, altitude=0, ktas_values=[130.0])
    (high_speed_point,) = sustained_turn_profile(adc, weight=17.4, altitude=0, ktas_values=[300.0])

    # Confirms both points turn at all, so the comparison means something.
    assert low_speed_point.max_phad_cells > 0
    assert high_speed_point.sustained_phad_cells > 0
    assert low_speed_point.sustained_turn_cells == pytest.approx(low_speed_point.max_phad_cells)
    assert high_speed_point.sustained_turn_cells == pytest.approx(high_speed_point.sustained_phad_cells)


def test_turn_rate_holds_across_the_j6c_fp_step():
    # Regression test: J-6C dry at alt 75, weight 14 -- the speed FP steps
    # from 6 to 7 at 260kt, where the old 2 x load / FP rule of thumb
    # dipped. The turn rate now comes from true airspeed, so it keeps
    # rising with max_load across the step.
    adc = AircraftDataCard.from_json(REPO_ROOT / "adc" / "j-6c.json")
    ktas_values = [250.0, 260.0, 270.0, 280.0]
    points = sustained_turn_profile(adc, weight=14, altitude=75, ktas_values=ktas_values, afterburner=False)
    whole = sustained_turn_profile(
        adc, weight=14, altitude=75, ktas_values=ktas_values, afterburner=False, fractional=False
    )

    assert [p.speed_fp for p in points] == [6, 7, 7, 7]
    assert [p.max_pullable_cells for p in points] == pytest.approx([2.67, 2.79, 2.89, 2.99], abs=0.01)
    assert [p.max_load for p in whole] == [9, 10, 11, 11]
    # Whole cells round down: the leftover degrees are lost each turn.
    assert [p.max_pullable_cells for p in whole] == [2, 2, 2, 2]


def test_sustained_turn_profile_caps_the_turn_rate_where_max_load_departs_flight():
    # FJ-3M dry at alt 75: at 550kt the wing allows 71 loads, but anything
    # from 59.7 up ends the turn at 0 KTAS or less. At 400kt the wing gives
    # out first.
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)
    state = AircraftState(adc, weight=17.4, ktas=550.0, altitude=75)
    fast, slow = sustained_turn_profile(
        adc, weight=17.4, altitude=75, ktas_values=[550.0, 400.0], afterburner=False
    )
    fast_whole, slow_whole = sustained_turn_profile(
        adc, weight=17.4, altitude=75, ktas_values=[550.0, 400.0], afterburner=False, fractional=False
    )

    assert fast.max_pullable_load == pytest.approx(departure_load(state, afterburner=False))
    assert fast.max_pullable_load < fast.max_load
    assert fast.max_pullable_cells == pytest.approx(phad_cells_from_load(fast.max_pullable_load, 550.0))
    assert slow.max_pullable_load == pytest.approx(slow.max_load)

    # Whole loads: 59 still flies, 60 doesn't.
    assert fast_whole.max_pullable_load == 59
    assert calculate_performance(state, 59, afterburner=False).new_state.ktas > 0
    assert calculate_performance(state, 60, afterburner=False).new_state.ktas <= 0
    assert slow_whole.max_pullable_load == slow_whole.max_load


def test_sustained_turn_profile_speed_fp_uses_raw_ktas_not_keas():
    # Regression test: speed_fp used to be computed from state.get_keas()
    # (matching calculate_performance()'s own internal convention), but
    # speed_fp_from_ktas() takes its name, and every other place that reads
    # FP off a state (the turn screen's stat bar, main()'s printed KTAS/FP
    # line), uses raw ktas -- confirmed directly: 120 KTAS is 3 FP
    # (2 + (120-60)//40), not the 2 FP get_keas() would give at a nonzero
    # altitude like 75.
    adc = make_ab_adc()

    (point,) = sustained_turn_profile(adc, weight=17.4, altitude=75, ktas_values=[120.0])

    assert point.speed_fp == 3


def test_sustained_turn_profile_afterburner_toggle_threads_through():
    adc = make_ab_adc()

    (dry_point,) = sustained_turn_profile(
        adc, weight=17.4, altitude=0, ktas_values=[325.0], afterburner=False
    )
    (ab_point,) = sustained_turn_profile(
        adc, weight=17.4, altitude=0, ktas_values=[325.0], afterburner=True
    )

    assert ab_point.sustained_load > dry_point.sustained_load
    assert ab_point.engine_output > dry_point.engine_output


# ---------------------------------------------------------------------------
# find_best_sustained_turn
# ---------------------------------------------------------------------------

def _sustained_point(ktas, sustained_load, max_load, sustained_turn_cells=None):
    # The finders only look at ktas, the two loads and sustained_turn_cells
    # -- the rest is filler for these synthetic scenarios. Cells default to
    # what a whole-number profile would compute.
    sustained_phad_cells = phad_cells_from_load(sustained_load, ktas)
    max_phad_cells = phad_cells_from_load(max_load, ktas)
    if sustained_turn_cells is None:
        sustained_turn_cells = whole_phad_cells(
            phad_cells_from_load(min(sustained_load, max_load), ktas)
        )
    return SustainedTurnPoint(
        ktas=ktas, mach=0.5, speed_fp=speedbop.speed_fp_from_ktas(ktas),
        sustained_load=sustained_load, max_load=max_load,
        sustained_phad_cells=sustained_phad_cells,
        max_phad_cells=max_phad_cells,
        max_pullable_load=max_load,
        max_pullable_cells=whole_phad_cells(max_phad_cells),
        sustained_turn_cells=sustained_turn_cells,
        engine_output=0.0, total_drag=0.0,
    )


def test_find_best_sustained_turn_interpolates_the_crossing():
    points = [
        _sustained_point(100.0, sustained_load=10.0, max_load=5.0),  # sustained > max
        _sustained_point(200.0, sustained_load=8.0, max_load=9.0),   # sustained < max
    ]

    best = find_best_sustained_turn(points)

    # diff(100) = 10-5 = 5; diff(200) = 8-9 = -1; crossing at t = 5/(5-(-1)) = 5/6
    assert isinstance(best, BestSustainedTurn)
    assert best.ktas == pytest.approx(100.0 + (5 / 6) * 100.0)
    assert best.load == pytest.approx(10.0 + (5 / 6) * (8.0 - 10.0))
    assert best.phad_cells == pytest.approx(phad_cells_from_load(best.load, best.ktas))


def test_find_best_sustained_turn_handles_exact_equality_at_a_sample_point():
    points = [
        _sustained_point(100.0, sustained_load=10.0, max_load=10.0),  # exact crossing here
        _sustained_point(200.0, sustained_load=8.0, max_load=12.0),
    ]

    best = find_best_sustained_turn(points)

    assert best.ktas == pytest.approx(100.0)
    assert best.load == pytest.approx(10.0)


def test_find_best_sustained_turn_returns_none_when_curves_never_cross():
    points = [
        _sustained_point(100.0, sustained_load=20.0, max_load=5.0),
        _sustained_point(200.0, sustained_load=15.0, max_load=8.0),
    ]  # sustained_load stays above max_load throughout

    assert find_best_sustained_turn(points) is None


def test_find_best_sustained_turn_returns_the_first_crossing_found():
    # The curves cross twice here -- the function returns the first one
    # walking the list in order, not "the best" by any other criterion.
    points = [
        _sustained_point(100.0, sustained_load=10.0, max_load=5.0),   # sustained > max
        _sustained_point(200.0, sustained_load=5.0, max_load=10.0),   # sustained < max (1st crossing)
        _sustained_point(300.0, sustained_load=12.0, max_load=8.0),   # sustained > max again (2nd)
    ]

    best = find_best_sustained_turn(points)

    assert 100.0 < best.ktas < 200.0


def test_find_best_sustained_turn_waits_for_sustained_to_drop_below_max():
    # In whole loads the two curves often touch at low speed and run level
    # together. Touching isn't the crossing -- the marker belongs where
    # sustained finally drops below max_load, not at the first equal point.
    points = [
        _sustained_point(100.0, sustained_load=3, max_load=3),
        _sustained_point(110.0, sustained_load=4, max_load=4),
        _sustained_point(120.0, sustained_load=4, max_load=5),
    ]

    best = find_best_sustained_turn(points)

    assert best.ktas == pytest.approx(110.0)
    assert best.load == pytest.approx(4.0)


def test_find_best_sustained_turn_on_the_real_fj3m_in_whole_loads():
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)
    points = sustained_turn_profile(
        adc, weight=17.4, altitude=75,
        ktas_values=[float(k) for k in range(0, 801, 10)], afterburner=False, fractional=False,
    )

    best = find_best_sustained_turn(points)

    assert best.ktas == pytest.approx(160.0)
    assert best.load == pytest.approx(4.0)


def test_find_best_sustained_turn_on_the_real_fj3m_unrounded():
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)
    points = sustained_turn_profile(
        adc, weight=17.4, altitude=75,
        ktas_values=[float(k) for k in range(0, 801, 10)], afterburner=False,
    )

    best = find_best_sustained_turn(points)

    assert best.ktas == pytest.approx(157.4, abs=0.1)
    assert best.load == pytest.approx(4.17, abs=0.01)


def test_find_best_sustained_turn_skips_the_zero_airspeed_dead_zone():
    # Regression test: a profile starting at ktas=0 has max_load=0 there
    # (get_max_load() bottoms out at 0 the same way get_sustained_load()
    # does, since get_smash() is 0 too) -- sustained_load=0 as well, so the
    # very first pair used to look like an exact "crossing" at ktas=0,
    # load=0, even though it's really just "the aircraft can't fly yet,"
    # not a meaningful sustained turn point. The real crossing further
    # along the curve should be found instead.
    points = [
        _sustained_point(0.0, sustained_load=0.0, max_load=0.0),
        _sustained_point(50.0, sustained_load=0.0, max_load=0.0),
        _sustained_point(100.0, sustained_load=10.0, max_load=5.0),
        _sustained_point(200.0, sustained_load=8.0, max_load=9.0),
    ]

    best = find_best_sustained_turn(points)

    assert best.ktas > 100.0
    assert best.load > 0


def test_find_best_sustained_turn_on_a_real_profile():
    adc = make_ab_adc()
    ktas_values = [float(k) for k in range(0, 600, 10)]
    points = sustained_turn_profile(adc, weight=17.4, altitude=0, ktas_values=ktas_values)

    best = find_best_sustained_turn(points)

    assert best is not None
    assert 0 < best.ktas < 600
    assert best.load > 0


# ---------------------------------------------------------------------------
# find_best_sustained_turn_rate
# ---------------------------------------------------------------------------

def test_find_best_sustained_turn_rate_returns_the_slowest_speed_at_the_best_rate():
    # Cells given directly -- the finder only reads them.
    points = [
        _sustained_point(100.0, sustained_load=4, max_load=2, sustained_turn_cells=1),
        _sustained_point(110.0, sustained_load=4, max_load=6, sustained_turn_cells=2),
        _sustained_point(120.0, sustained_load=5, max_load=7, sustained_turn_cells=2),
        _sustained_point(130.0, sustained_load=3, max_load=7, sustained_turn_cells=1),
        _sustained_point(140.0, sustained_load=5, max_load=8, sustained_turn_cells=2),  # again
    ]

    best = find_best_sustained_turn_rate(points)

    assert isinstance(best, BestSustainedTurnRate)
    assert best.cells == 2
    assert best.ktas == pytest.approx(110.0)
    # The lower of sustained and max load there.
    assert best.load == 4


def test_find_best_sustained_turn_rate_can_differ_from_the_crossing():
    # The crossing (where max-load turns settle) is at 110kt, where 4 loads
    # turn 1 cell; at 200kt, 8 sustainable loads turn 2.
    points = [
        _sustained_point(100.0, sustained_load=5, max_load=4),
        _sustained_point(110.0, sustained_load=4, max_load=4),
        _sustained_point(120.0, sustained_load=4, max_load=6),
        _sustained_point(200.0, sustained_load=8, max_load=12),
    ]

    crossing = find_best_sustained_turn(points)
    best = find_best_sustained_turn_rate(points)

    assert crossing.ktas == pytest.approx(110.0)
    assert points[1].sustained_turn_cells == 1
    assert best.cells == 2
    assert best.ktas == pytest.approx(200.0)


def test_find_best_sustained_turn_rate_returns_none_when_nothing_holds_a_cell():
    points = [
        _sustained_point(100.0, sustained_load=0, max_load=3),
        _sustained_point(200.0, sustained_load=3, max_load=5),  # 1 G: no turn at all
    ]

    assert find_best_sustained_turn_rate(points) is None
    assert find_best_sustained_turn_rate([]) is None


def test_find_best_sustained_turn_rate_on_the_real_fj3m_in_whole_numbers():
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)
    points = sustained_turn_profile(
        adc, weight=17.4, altitude=75,
        ktas_values=[float(k) for k in range(0, 801, 10)], afterburner=False, fractional=False,
    )

    best = find_best_sustained_turn_rate(points)

    # 1 whole cell holds from 160 to 480kt; the slowest turns it tightest.
    assert best == BestSustainedTurnRate(cells=1, ktas=160.0, load=4)


def test_find_best_sustained_turn_rate_on_the_real_fj3m_unrounded():
    # Unrounded, the rate is nearly flat from 250 to 340kt (1.67-1.70
    # cells); 320kt edges out the rest on the E6B scales' rounding.
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)
    points = sustained_turn_profile(
        adc, weight=17.4, altitude=75,
        ktas_values=[float(k) for k in range(0, 801, 10)], afterburner=False,
    )

    best = find_best_sustained_turn_rate(points)

    assert best.cells == pytest.approx(1.70, abs=0.01)
    assert best.ktas == pytest.approx(320.0)
    assert best.load == pytest.approx(8.05, abs=0.01)


# ---------------------------------------------------------------------------
# find_structural_corner_speed
# ---------------------------------------------------------------------------

def test_find_structural_corner_speed_interpolates_the_crossing():
    points = [
        _sustained_point(100.0, sustained_load=0.0, max_load=5.0),
        _sustained_point(200.0, sustained_load=0.0, max_load=9.0),
    ]

    corner = find_structural_corner_speed(points, combat_safe_load=7.0)

    assert isinstance(corner, StructuralCornerPoint)
    # max_load(100)=5, max_load(200)=9 -- 7 is 2/4 of the way there.
    assert corner.ktas == pytest.approx(150.0)
    assert corner.load == pytest.approx(7.0)


def test_find_structural_corner_speed_handles_exact_equality_at_a_sample_point():
    points = [
        _sustained_point(100.0, sustained_load=0.0, max_load=7.0),
        _sustained_point(200.0, sustained_load=0.0, max_load=9.0),
    ]

    corner = find_structural_corner_speed(points, combat_safe_load=7.0)

    assert corner.ktas == pytest.approx(100.0)
    assert corner.load == pytest.approx(7.0)


def test_find_structural_corner_speed_returns_the_first_point_when_already_past_it():
    # The structural G rating is already exceeded at the very first swept
    # point -- the true crossing is below the swept range, so this snaps to
    # the leftmost sample instead of extrapolating past it.
    points = [
        _sustained_point(100.0, sustained_load=0.0, max_load=12.0),
        _sustained_point(200.0, sustained_load=0.0, max_load=20.0),
    ]

    corner = find_structural_corner_speed(points, combat_safe_load=7.0)

    assert corner.ktas == pytest.approx(100.0)
    assert corner.load == pytest.approx(7.0)


def test_find_structural_corner_speed_returns_none_when_never_reached():
    points = [
        _sustained_point(100.0, sustained_load=0.0, max_load=2.0),
        _sustained_point(200.0, sustained_load=0.0, max_load=4.0),
    ]

    assert find_structural_corner_speed(points, combat_safe_load=7.0) is None


def test_find_structural_corner_speed_on_a_real_profile():
    adc = make_ab_adc()  # combat_safe_load=10.0
    ktas_values = [float(k) for k in range(0, 600, 10)]
    points = sustained_turn_profile(adc, weight=17.4, altitude=0, ktas_values=ktas_values)

    corner = find_structural_corner_speed(points, adc.characteristics.combat_safe_load)

    assert corner is not None
    assert 0 < corner.ktas < 600
    assert corner.load == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# engine_scale / pressure_ratio (the E6B's p-alt/mach engine window)
# ---------------------------------------------------------------------------

ENGINE_SCALE_READINGS = REPO_ROOT / "e6b" / "mach_engine.csv"


def test_engine_scale_is_one_at_sea_level_and_mach_0():
    assert engine_scale(0, 0.0) == pytest.approx(1.0)


def test_pressure_ratio_is_continuous_at_the_tropopause():
    tropopause = 36089.0 / FEET_PER_ALTITUDE_UNIT
    assert pressure_ratio(tropopause - 1e-6) == pytest.approx(
        pressure_ratio(tropopause + 1e-6), rel=1e-4
    )


def test_engine_scale_matches_the_e6b_readings():
    # Readings taken off the physical window (altitude set over mach, ratio
    # read on the outer rings).
    readings = load_samples(str(ENGINE_SCALE_READINGS))
    assert len(readings) == 36

    for r in readings:
        assert engine_scale(r["alt"], r["mach"]) == pytest.approx(r["engine_scale"], rel=0.03), r


def test_engine_scale_reproduces_scenario_7():
    # TEST_PLAN.md #7: manual pass read engine output 47 -> base 39 -> 36
    # after weight scaling. speedbop reads 46.1 off the J65 chart.
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)
    state = make_state(adc=adc, weight=17.4, ktas=485.0, altitude=75)

    assert 47 / state.get_engine_scale() == pytest.approx(39, rel=0.03)
    assert state.get_engine_delta_ktas(state.get_engine_output(False)) == pytest.approx(35.4, abs=0.1)


# ---------------------------------------------------------------------------
# Standard atmosphere: keas_from_ktas / mach_from_keas vs the E6B readings
# ---------------------------------------------------------------------------

KEAS_READINGS = REPO_ROOT / "e6b" / "samples" / "keas.csv"
MACH_READINGS = REPO_ROOT / "e6b" / "mach.csv"


def test_density_ratio_is_continuous_at_the_tropopause():
    tropopause = TROPOPAUSE_FT / FEET_PER_ALTITUDE_UNIT
    assert density_ratio(tropopause - 1e-6) == pytest.approx(
        density_ratio(tropopause + 1e-6), rel=1e-4
    )


def test_keas_from_ktas_matches_the_e6b_readings():
    # alt 175 / 500 KTAS reads 222 KEAS where both neighbors and the formula
    # give ~278 -- a suspected transcription slip, see TEST_PLAN.md.
    suspect = {(175.0, 500.0)}
    readings = [
        r for r in load_samples(str(KEAS_READINGS)) if (r["altitude"], r["ktas"]) not in suspect
    ]

    errors = []
    for r in readings:
        predicted = keas_from_ktas(r["ktas"], r["altitude"])
        assert predicted == pytest.approx(r["keas"], rel=0.04), r
        errors.append(abs(predicted / r["keas"] - 1))
    assert statistics.median(errors) < 0.005


def test_mach_from_keas_matches_the_e6b_readings():
    # Two readings sit 7-9% off an otherwise ~1% fit and look like reading
    # slips -- see TEST_PLAN.md.
    suspect = {(20.0, 500.0), (200.0, 420.0)}
    readings = [r for r in load_samples(str(MACH_READINGS)) if (r["alt"], r["keas"]) not in suspect]

    errors = []
    for r in readings:
        predicted = mach_from_keas(r["keas"], r["alt"])
        assert predicted == pytest.approx(r["mach"], rel=0.05), r
        errors.append(abs(predicted / r["mach"] - 1))
    assert statistics.median(errors) < 0.0125


# ---------------------------------------------------------------------------
# Inverse helpers
# ---------------------------------------------------------------------------

def test_q_from_smash_inverts_the_smash_formula():
    # smash = 10 * q / wl  =>  q = smash * wl / 10
    assert q_from_smash(smash=10.0, wl=5.0) == pytest.approx(5.0)


def test_keas_from_q_inverts_the_q_formula():
    # q = keas^2 / 2950
    assert keas_from_q(q=100**2 / 2950) == pytest.approx(100.0)


def test_ktas_from_keas_is_identity_at_sea_level():
    # keas == ktas at altitude 0 by definition, same boundary condition
    # get_keas() and e6b/samples/keas.csv were built around.
    assert ktas_from_keas(keas=123.0, altitude=0) == pytest.approx(123.0)


def test_ktas_from_keas_inverts_keas_from_ktas():
    # Alt 75 is 15,000 ft: sqrt(density ratio) = 0.79322.
    assert ktas_from_keas(keas=200.0, altitude=75) == pytest.approx(200.0 / 0.79322, rel=1e-4)
    assert keas_from_ktas(ktas_from_keas(200.0, 75), 75) == pytest.approx(200.0)


def test_ktas_from_q_chains_keas_from_q_and_ktas_from_keas():
    q, altitude = 16.7, 75
    assert ktas_from_q(q, altitude) == pytest.approx(
        ktas_from_keas(keas_from_q(q), altitude)
    )


def test_inverse_helpers_round_trip_the_real_fixture_within_rounding_error():
    # get_q()/get_smash() round to 1 decimal, so inverting a rounded
    # reading recovers the original value only approximately -- this
    # documents how much error that rounding introduces, not exact
    # equality.
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)
    state = make_state(adc=adc, weight=17.4, ktas=485.0, altitude=75)

    q = state.get_q()
    smash = state.get_smash()
    wing_load = state.get_wing_load()

    assert q_from_smash(smash, wing_load) == pytest.approx(q, abs=0.3)
    assert keas_from_q(q) == pytest.approx(state.get_keas(), abs=1.0)
    assert ktas_from_q(q, state.altitude) == pytest.approx(state.ktas, rel=0.02)


# ---------------------------------------------------------------------------
# corner_speed
# ---------------------------------------------------------------------------

def test_corner_speed_on_the_real_fixture():
    # 22.4/3.8 alpha-over-LCS (the 485kt state's mach 0.79 reads the 0.84
    # lift row), 21 combat safe load, 58.0 wing loading -> 246 KEAS.
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)
    state = make_state(adc=adc, weight=17.4, ktas=485.0, altitude=75)

    assert corner_speed(state) == 246

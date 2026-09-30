import dataclasses
import json
import math

import pytest

import speedbop
from speedbop import (
    AircraftDataCard,
    AircraftState,
    PerformanceHistory,
    TurnPerformance,
    _bop_tablerow_lookup,
    _dataclass_from_dict,
    calculate_performance,
    gs_from_pulls,
)
from chart import IsobarChart, load_isobars
from factories import REAL_ADC_PATH, REPO_ROOT, make_ab_adc, make_adc, make_state
from performance import engine_scale


# ---------------------------------------------------------------------------
# _dataclass_from_dict
# ---------------------------------------------------------------------------

def test_dataclass_from_dict_passes_scalars_through_unchanged():
    # klass isn't a dataclass, so dataclasses.fields(klass) raises and the
    # bare except falls back to returning the raw value as-is.
    assert _dataclass_from_dict(str, "hello") == "hello"
    assert _dataclass_from_dict(float, 3.0) == 3.0


def test_dataclass_from_dict_builds_nested_dataclasses():
    @dataclasses.dataclass(frozen=True)
    class Inner:
        x: float

    @dataclasses.dataclass(frozen=True)
    class Outer:
        name: str
        inner: Inner

    result = _dataclass_from_dict(Outer, {"name": "a", "inner": {"x": 1.5}})

    assert result == Outer(name="a", inner=Inner(x=1.5))
    assert isinstance(result.inner, Inner)


def test_dataclass_from_dict_ignores_unrecognized_extra_keys():
    # A key not in the dataclass's fields is skipped, rather than (as
    # before this fix) causing the WHOLE object to silently fall back to
    # the raw dict via the bare `except:`. This was a real production bug:
    # adc/fj-3m.json gained a "form_table" key ahead of any code consuming
    # it, which broke AircraftDataCard.from_json() (and therefore main())
    # entirely -- confirmed and fixed in this session.
    @dataclasses.dataclass(frozen=True)
    class Simple:
        x: float

    d = {"x": 1.0, "unexpected_extra_key": 2.0}

    result = _dataclass_from_dict(Simple, d)

    assert result == Simple(x=1.0)


def test_dataclass_from_dict_silently_skips_conversion_on_missing_key():
    # A missing required field makes klass(**{...}) raise TypeError --
    # caught by the bare `except:`, so the whole object falls back to the
    # raw dict. Unlike the extra-key case above, this one is NOT fixed --
    # documented here as a known remaining gotcha.
    @dataclasses.dataclass(frozen=True)
    class Simple:
        x: float
        y: float

    d = {"x": 1.0}  # missing required "y"

    result = _dataclass_from_dict(Simple, d)

    assert result is d
    assert not isinstance(result, Simple)


def test_dataclass_from_dict_list_branch_has_a_name_error_bug():
    # BUG (speedbop.py, _dataclass_from_dict): the list branch iterates
    # over `data`, but the function's parameter is named `d` -- `data` is
    # never defined, so any list-typed field raises NameError instead of
    # converting. This test documents the current (broken) behavior rather
    # than silently working around it.
    with pytest.raises(NameError):
        _dataclass_from_dict(list[str], ["a", "b"])


# ---------------------------------------------------------------------------
# _bop_tablerow_lookup
# ---------------------------------------------------------------------------

def test_bop_tablerow_lookup_finds_the_ceiling_entry():
    # Returns the first (smallest) key >= value -- a "round up to the next
    # table entry" lookup, matching how get_lcs() reads a mach breakpoint
    # table.
    table = {0.5: (4.0, 100), 1.0: (2.0, 50)}

    assert _bop_tablerow_lookup(0.3, table) == (4.0, 100)
    assert _bop_tablerow_lookup(0.5, table) == (4.0, 100)
    assert _bop_tablerow_lookup(0.6, table) == (2.0, 50)


def test_bop_tablerow_lookup_clamps_above_the_highest_key():
    table = {0.5: (4.0, 100), 1.0: (2.0, 50)}

    assert _bop_tablerow_lookup(5.0, table) == (2.0, 50)


def test_bop_tablerow_lookup_handles_string_keys_from_json():
    # Real tables loaded via from_json keep string keys (JSON object keys
    # are always strings, and _dataclass_from_dict doesn't convert dict
    # values), which is why this calls float(key) internally.
    table = {"0.72": [4.7, 328], "0.84": [3.8, 232]}

    assert _bop_tablerow_lookup(0.8, table) == [3.8, 232]


# ---------------------------------------------------------------------------
# AircraftDataCard
# ---------------------------------------------------------------------------


def test_aircraft_data_card_is_frozen():
    adc = make_adc()

    with pytest.raises(dataclasses.FrozenInstanceError):
        adc.name = "New Name"


def test_aircraft_data_card_from_json_builds_nested_dataclasses(tmp_path):
    (tmp_path / "engine.csv").write_text(
        "output,altitude,mach\n"
        "10,0,0.5\n"
        "10,100,1.0\n"
        "20,0,0.2\n"
        "20,100,0.6\n"
    )
    path = tmp_path / "plane.json"
    path.write_text(json.dumps({
        "name": "Test Plane",
        "version": "2.3",
        "lift": {"alpha_max": 15.0, "mach_lcs_ids_table": {"0.5": [4.0, 100]}},
        "characteristics": {"wing_area": 4.0, "combat_safe_load": 12.0},
        "form": {"brake": 33, "mach_to_drag_table": {"0.5": 10}},
        "roll_rate": {"0.5": "Slow", "1.0": "Fast"},
        "stores": {"combat_weight": 8.5},
        "dry_engine_output": "engine.csv",
    }))

    adc = AircraftDataCard.from_json(path)

    assert adc.name == "Test Plane"
    assert adc.version == "2.3"
    assert isinstance(adc.lift, AircraftDataCard.Lift)
    assert adc.lift.alpha_max == 15.0
    assert isinstance(adc.characteristics, AircraftDataCard.Characteristics)
    assert adc.characteristics.wing_area == 4.0
    assert adc.characteristics.combat_safe_load == 12.0
    assert isinstance(adc.form, AircraftDataCard.Form)
    assert adc.form.mach_to_drag_table == {"0.5": 10}
    assert isinstance(adc.stores, AircraftDataCard.Stores)
    assert adc.stores.combat_weight == 8.5
    assert isinstance(adc.dry_engine_output, IsobarChart)
    assert adc.dry_engine_output.interpolate(altitude=0, mach=0.5) == pytest.approx(10.0)
    assert adc.roll_rate == {"0.5": "Slow", "1.0": "Fast"}
    # No ab_engine_output key in the JSON -- from_json() falls back to None
    # rather than requiring every aircraft to have an afterburner.
    assert adc.ab_engine_output is None


def test_aircraft_data_card_from_json_reads_ab_engine_output_when_present(tmp_path):
    (tmp_path / "dry.csv").write_text(
        "output,altitude,mach\n10,0,0.5\n10,100,1.0\n20,0,0.2\n20,100,0.6\n"
    )
    (tmp_path / "ab.csv").write_text(
        "output,altitude,mach\n50,0,0.5\n50,100,1.0\n80,0,0.2\n80,100,0.6\n"
    )
    path = tmp_path / "plane.json"
    path.write_text(json.dumps({
        "name": "Test Plane",
        "version": "2.3",
        "lift": {"alpha_max": 15.0, "mach_lcs_ids_table": {"0.5": [4.0, 100]}},
        "characteristics": {"wing_area": 4.0, "combat_safe_load": 12.0},
        "form": {"brake": 33, "mach_to_drag_table": {"0.5": 10}},
        "roll_rate": {"0.5": "Slow", "1.0": "Fast"},
        "stores": {"combat_weight": 8.5},
        "dry_engine_output": "dry.csv",
        "ab_engine_output": "ab.csv",
    }))

    adc = AircraftDataCard.from_json(path)

    assert isinstance(adc.ab_engine_output, IsobarChart)
    # The AB and dry charts are genuinely different data, not the same file
    # loaded twice under two names.
    assert adc.ab_engine_output.interpolate(altitude=0, mach=0.5) != pytest.approx(
        adc.dry_engine_output.interpolate(altitude=0, mach=0.5)
    )


def test_aircraft_data_card_from_json_requires_dry_engine_output_key(tmp_path):
    # from_json's chart-loading step indexes adc_dict["dry_engine_output"]
    # directly (not .get()), outside _dataclass_from_dict's lenient bare
    # except -- so a data card with no engine chart at all currently
    # crashes hard rather than loading with a missing/empty chart. Worth
    # revisiting if some future aircraft shouldn't need one; documented
    # here rather than silently changed.
    path = tmp_path / "plane.json"
    path.write_text(json.dumps({
        "name": "Test Plane",
        "version": "2.3",
        "lift": {"alpha_max": 15.0, "mach_lcs_ids_table": {"0.5": [4.0, 100]}},
        "characteristics": {"wing_area": 4.0, "combat_safe_load": 12.0},
        "stores": {"combat_weight": 8.5},
    }))

    with pytest.raises(KeyError, match="dry_engine_output"):
        AircraftDataCard.from_json(path)


def test_aircraft_data_card_from_json_reads_real_fixture():
    # Regression test against the actual repo fixture used by main().
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)

    assert adc.name == "FJ-3M Fury"
    assert adc.version == "1.25.01"
    assert adc.characteristics.wing_area == pytest.approx(3.0)
    assert adc.characteristics.combat_safe_load == pytest.approx(21)
    assert adc.stores.combat_weight == pytest.approx(15.7)
    assert adc.lift.alpha_max == pytest.approx(22.4)
    assert adc.lift.mach_lcs_ids_table["0.84"] == [3.8, 232]
    assert isinstance(adc.dry_engine_output, IsobarChart)


# ---------------------------------------------------------------------------
# AircraftState
# ---------------------------------------------------------------------------

def test_get_wing_load():
    adc = make_adc(characteristics=AircraftDataCard.Characteristics(
        wing_area=3.0, combat_safe_load=21.0,
    ))
    state = make_state(adc=adc, weight=17.4)

    assert state.get_wing_load() == pytest.approx(round(17.4 / 3.0 * 10.0, 1))


def test_get_safe_load():
    adc = make_adc(
        characteristics=AircraftDataCard.Characteristics(wing_area=3.0, combat_safe_load=21.0),
        stores=AircraftDataCard.Stores(combat_weight=15.7),
    )
    state = make_state(adc=adc, weight=17.4)

    assert state.get_safe_load() == pytest.approx(round(15.7 / 17.4 * 21.0, 1))


def test_get_keas_at_sea_level_equals_ktas():
    # KEAS == KTAS at altitude 0 by definition (exp(0.003358*0) == 1) --
    # the same boundary condition e6b/samples/keas.csv was fit against.
    state = make_state(ktas=123.0, altitude=0)

    assert state.get_keas() == 123


def test_get_keas_applies_altitude_correction_and_rounds():
    state = make_state(ktas=285.0, altitude=75)

    # keas = round(285 / exp(0.003358*75)) -- rounds because KEAS is read
    # off the device as a whole number, same granularity as e6b/keas.csv.
    assert state.get_keas() == round(285.0 / math.exp(0.003358 * 75))
    assert isinstance(state.get_keas(), int)


def test_get_q_uses_rounded_keas():
    # q = round(keas^2 / 2950, 1), using the rounded get_keas() result --
    # not the raw unrounded ratio -- so this pins the two methods'
    # composition, not just the formula in isolation.
    state = make_state(ktas=100.0, altitude=0)  # keas rounds to exactly 100

    assert state.get_q() == pytest.approx(round(100**2 / 2950, 1))


def test_get_smash_uses_wing_load_not_a_raw_weight():
    # smash = round(10 * q / get_wing_load(), 1) -- confirms it's composed
    # from the *wing load* value (weight/wing_area*10) and the already-
    # rounded get_q(), not raw aircraft weight or an unrounded q, matching
    # the "wl" variable e6b/smash.csv was fit against.
    adc = make_adc(characteristics=AircraftDataCard.Characteristics(
        wing_area=3.0, combat_safe_load=21.0,
    ))
    state = make_state(adc=adc, weight=17.4, ktas=100.0, altitude=0)

    expected_q = round(100**2 / 2950, 1)
    expected_wing_load = 17.4 / 3.0 * 10.0
    assert state.get_smash() == pytest.approx(round(10.0 * expected_q / expected_wing_load, 1))


def test_get_mach_is_the_inverse_of_the_keas_formula():
    # keas = 670.0 * mach * exp(-0.0044*altitude), so mach = keas *
    # exp(0.0044*altitude) / 670.0 -- and at altitude=0 that's just
    # keas/670, the cleanest case to pin down independent of the
    # exponential term.
    state = make_state(ktas=700.0, altitude=0)

    assert state.get_mach() == pytest.approx(round(700 / 670.0, 2))


def test_get_mach_keeps_two_decimals_so_table_lookups_pick_the_right_row():
    # Regression test (TEST_PLAN.md #7): FJ-3M at 485 KTAS/alt 75 is mach
    # 0.783. Rounding to one decimal (0.8) skipped past the 0.78 lift row
    # and 0.79 drag row the slide-rule procedure actually selects.
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)
    state = make_state(adc=adc, weight=17.4, ktas=485.0, altitude=75)

    assert state.get_mach() == pytest.approx(0.78)
    assert state.get_lcs() == pytest.approx(4.2)
    assert state.get_ids() == 270
    assert state.get_form_drag() == pytest.approx(24.0)


def test_get_mach_uses_rounded_keas_and_applies_altitude_correction():
    # Cross-checks against a real e6b/mach.csv reading (keas=250, alt=225,
    # mach=1.0) -- ktas is chosen so get_keas() rounds to exactly 250.
    state = make_state(ktas=250 * math.exp(0.003358 * 225), altitude=225)

    assert state.get_keas() == 250
    assert state.get_mach() == pytest.approx(1.0, abs=0.02)


def test_get_lcs_looks_up_by_mach():
    # ktas/altitude chosen so get_mach() lands exactly on 0.5, which is a
    # key in the synthetic table -- _bop_tablerow_lookup returns that
    # entry directly, and get_lcs() takes its first element.
    state = make_state(ktas=337.3, altitude=0)

    assert state.get_mach() == pytest.approx(0.5)
    assert state.get_lcs() == pytest.approx(4.0)


def test_get_ids_looks_up_by_mach():
    # Same lookup as get_lcs(), but takes the table row's second element.
    state = make_state(ktas=337.3, altitude=0)

    assert state.get_mach() == pytest.approx(0.5)
    assert state.get_ids() == pytest.approx(100)


# ---------------------------------------------------------------------------
# AircraftState.get_roll_rate()
# ---------------------------------------------------------------------------

def test_get_roll_rate_looks_up_by_smash():
    # Table keys arrive as strings from JSON (see roll_rate: dict[float,
    # str] in AircraftDataCard, loaded verbatim by _dataclass_from_dict
    # since dict fields aren't recursed into) -- get_roll_rate() must
    # tolerate that, same as get_lcs()/get_ids() do via _bop_tablerow_lookup.
    adc = make_adc(roll_rate={"0.8": "Slow", "4.1": "Med", "999": "Fast"})
    state = make_state(adc=adc, weight=17.4, ktas=100.0, altitude=0)

    # Default fixture (weight=17.4, wing_area=2.0, ktas=100, altitude=0)
    # resolves to smash=0.4, which sits below the first breakpoint.
    assert state.get_smash() == pytest.approx(0.4)
    assert state.get_roll_rate() == "Slow"


def test_get_roll_rate_picks_the_next_higher_breakpoint():
    adc = make_adc(roll_rate={"0.3": "Slow", "0.4": "Med", "999": "Fast"})
    state = make_state(adc=adc, weight=17.4, ktas=100.0, altitude=0)

    assert state.get_smash() == pytest.approx(0.4)
    assert state.get_roll_rate() == "Med"


def test_get_roll_rate_clamps_above_the_highest_smash_entry():
    adc = make_adc(roll_rate={"0.1": "Slow", "0.2": "Med"})
    state = make_state(adc=adc, weight=17.4, ktas=100.0, altitude=0)

    assert state.get_smash() == pytest.approx(0.4)
    assert state.get_roll_rate() == "Med"


def test_get_engine_output_matches_the_isobar_chart_directly():
    # get_engine_output() should be a thin, rounded wrapper around the
    # chart embedded in the aircraft's own data card.
    state = make_state(ktas=337.3, altitude=0)

    expected = round(
        state.adc.dry_engine_output.interpolate(altitude=state.altitude, mach=state.get_mach()), 1
    )
    assert state.get_engine_output() == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Afterburner (AircraftState.get_engine_output(afterburner=...))
# ---------------------------------------------------------------------------


def test_get_engine_output_defaults_to_afterburner_when_available():
    state = make_state(adc=make_ab_adc(), ktas=337.3, altitude=0)  # mach rounds to 0.5

    assert state.get_engine_output() == pytest.approx(50.0)  # from the AB chart, not dry's 30.0


def test_get_engine_output_afterburner_false_uses_dry_chart():
    state = make_state(adc=make_ab_adc(), ktas=337.3, altitude=0)

    assert state.get_engine_output(afterburner=False) == pytest.approx(30.0)


def test_get_engine_output_falls_back_to_dry_when_aircraft_has_no_afterburner():
    # make_adc()'s default ab_engine_output is None -- afterburner=True
    # (the default) must not crash or misbehave for an aircraft that simply
    # doesn't have one; it should transparently use the dry chart.
    state = make_state(ktas=337.3, altitude=0)

    assert state.get_engine_output(afterburner=True) == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# Form drag (AircraftState.get_form_drag / get_total_drag)
# ---------------------------------------------------------------------------

def _make_drag_adc(**overrides):
    form = AircraftDataCard.Form(
        brake=0, mach_to_drag_table={0.3: 10.0, 0.6: 20.0, 1.0: 40.0}
    )
    return make_adc(form=form, **overrides)


def test_get_form_drag_looks_up_by_mach():
    # Same ceiling-lookup semantics as get_lcs()/get_ids(): the first table
    # entry whose mach key is >= the query mach.
    state = make_state(adc=_make_drag_adc(), ktas=337.3, altitude=0)  # mach rounds to 0.5

    assert state.get_mach() == pytest.approx(0.5)
    assert state.get_form_drag() == pytest.approx(20.0)


def test_get_form_drag_clamps_above_the_highest_table_entry():
    state = make_state(adc=_make_drag_adc(), ktas=2000.0, altitude=0)

    assert state.get_form_drag() == pytest.approx(40.0)


def test_get_total_drag_currently_equals_form_drag_alone():
    # brake_drag and stores_drag are still hardcoded to 0 in
    # get_total_drag() (see its own TODO) -- this pins that current
    # behavior so a future change enabling them is a deliberate, visible
    # diff here rather than a silent behavior change.
    state = make_state(adc=_make_drag_adc(), ktas=337.3, altitude=0)

    assert state.get_total_drag() == pytest.approx(state.get_form_drag())


# ---------------------------------------------------------------------------
# speed_fp_from_ktas / real fixture
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ktas,expected", [
    (0.0, 1),
    (59.9, 1),
    (60.0, 2),
    (99.0, 2),
    (100.0, 3),
    (139.0, 3),
    (140.0, 4),
])
def test_speed_fp_from_ktas_steps_every_40_knots_above_60(ktas, expected):
    assert speedbop.speed_fp_from_ktas(ktas) == expected


def test_aircraft_state_against_real_fixture():
    # Matches main()'s own scenario exactly (weight=17.4, ktas=485,
    # altitude=75), so this doubles as a regression test for main()'s
    # printed output.
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)
    state = make_state(adc=adc, weight=17.4, ktas=485.0, altitude=75)

    assert state.get_wing_load() == pytest.approx(58.0)
    assert state.get_safe_load() == pytest.approx(18.9)
    assert state.get_keas() == 377
    expected_q = round(377**2 / 2950, 1)
    assert state.get_q() == pytest.approx(expected_q)
    assert state.get_smash() == pytest.approx(round(10.0 * expected_q / 58.0, 1))
    assert speedbop.speed_fp_from_ktas(state.ktas) == 12
    assert state.get_mach() == pytest.approx(0.78)
    assert state.get_engine_output() == pytest.approx(46.3)
    assert state.get_lcs() == pytest.approx(4.2)
    # smash=8.3 clears the fixture's highest roll_rate breakpoint (4.1).
    assert state.get_roll_rate() == "Fast"
    assert state.get_max_load() == 44


# ---------------------------------------------------------------------------
# calculate_performance / TurnPerformance
# ---------------------------------------------------------------------------

def test_calculate_performance_does_not_mutate_the_input_state():
    # Regression test: calculate_performance used to do `new_state = state;
    # new_state.ktas = new_ktas`, which is aliasing, not a copy -- it
    # silently mutated the caller's original state object too.
    state = make_state(ktas=485.0, altitude=75)
    original_ktas, original_altitude = state.ktas, state.altitude

    calculate_performance(state, segment_pulls=22, delta_altitude=-15)

    assert state.ktas == original_ktas
    assert state.altitude == original_altitude


def test_calculate_performance_updates_altitude_by_delta():
    # Regression test: delta_altitude used to only affect the gravity
    # term, never actually advancing state.altitude itself.
    state = make_state(ktas=485.0, altitude=75)

    performance = calculate_performance(state, segment_pulls=22, delta_altitude=-15)

    assert performance.new_state.altitude == 75 - 15


def test_calculate_performance_new_ktas_matches_the_reported_breakdown():
    state = make_state(ktas=485.0, altitude=75)

    performance = calculate_performance(state, segment_pulls=22, delta_altitude=-15)

    expected_new_ktas = (
        state.ktas
        - performance.induced_delta_ktas
        + performance.gravity_delta_ktas
        - performance.form_delta_ktas
        + performance.engine_delta_ktas
    )
    assert performance.new_state.ktas == pytest.approx(expected_new_ktas)


def test_calculate_performance_engine_delta_ktas_actually_speeds_up_the_aircraft():
    # Regression test: engine_delta_ktas used to be computed and reported
    # but never actually folded into new_ktas -- with no pulls and no
    # altitude change (so induced/gravity/form are all zero), the only
    # thing that should change ktas at all is engine thrust.
    state = make_state(ktas=100.0, altitude=0)

    performance = calculate_performance(state, segment_pulls=0, delta_altitude=0)

    assert performance.induced_delta_ktas == pytest.approx(0.0)
    assert performance.gravity_delta_ktas == pytest.approx(0.0)
    assert performance.engine_delta_ktas > 0
    assert performance.new_state.ktas == pytest.approx(
        state.ktas + performance.engine_delta_ktas
    )


def test_calculate_performance_engine_output_can_be_overridden():
    state = make_state(ktas=100.0, altitude=0)

    performance = calculate_performance(
        state, segment_pulls=0, delta_altitude=0, engine_output=12.5
    )

    assert performance.engine_output == pytest.approx(12.5)
    assert performance.engine_delta_ktas == pytest.approx(state.get_engine_delta_ktas(12.5))
    assert performance.new_state.ktas == pytest.approx(
        state.ktas + performance.engine_delta_ktas
    )


def test_engine_delta_ktas_scales_by_combat_weight_over_current_weight():
    # Base delta knots are at combat weight; an aircraft twice that heavy
    # gains half as much. Sea level at mach 0 keeps the engine scale at 1.
    state = make_state(weight=10.0, ktas=0.0, altitude=0)  # combat_weight=5.0

    assert state.get_engine_scale() == pytest.approx(1.0)
    assert state.get_engine_delta_ktas(40.0) == pytest.approx(20.0)


def test_engine_delta_ktas_is_unscaled_at_combat_weight_sea_level_and_mach_0():
    state = make_state(weight=5.0, ktas=0.0, altitude=0)  # combat_weight=5.0

    assert state.get_engine_delta_ktas(40.0) == pytest.approx(40.0)


def test_engine_delta_ktas_divides_by_the_engine_scale():
    state = make_state(weight=5.0, ktas=400.0, altitude=100)  # combat_weight=5.0

    assert state.get_engine_delta_ktas(40.0) == pytest.approx(
        40.0 / engine_scale(state.altitude, state.get_mach())
    )


def test_calculate_performance_applies_engine_scale_and_weight_scaling():
    state = make_state(ktas=100.0, altitude=0, weight=10.0)  # combat_weight=5.0

    performance = calculate_performance(
        state, segment_pulls=0, delta_altitude=0, engine_output=12.0
    )

    expected = 12.0 / state.get_engine_scale() * 0.5
    assert performance.engine_output == pytest.approx(12.0)
    assert performance.engine_scale == pytest.approx(state.get_engine_scale())
    assert performance.base_engine_delta_ktas == pytest.approx(12.0 / state.get_engine_scale())
    assert performance.engine_delta_ktas == pytest.approx(expected)
    assert performance.new_state.ktas == pytest.approx(state.ktas + expected)


# ---------------------------------------------------------------------------
# engine_scale / pressure_ratio (the E6B's p-alt/mach engine window)
# ---------------------------------------------------------------------------


def test_calculate_performance_engine_output_defaults_to_chart_max_when_not_given():
    state = make_state(ktas=100.0, altitude=0)

    performance = calculate_performance(state, segment_pulls=0, delta_altitude=0)

    assert performance.engine_output == pytest.approx(state.get_engine_output())


def test_calculate_performance_clamps_engine_output_to_chart_max():
    # You can't request more thrust than the engine actually has -- an
    # override above the chart's max for this state clamps down to it,
    # same treatment as the pulls/max-load clamp above.
    state = make_state(ktas=100.0, altitude=0)
    max_output = state.get_engine_output()

    performance = calculate_performance(
        state, segment_pulls=0, delta_altitude=0, engine_output=max_output + 50
    )

    assert performance.engine_output == pytest.approx(max_output)


def test_form_delta_ktas_is_drag_times_smash_over_ten():
    # Confirmed against the player aids (TEST_PLAN.md #7): FJ-3M at 485
    # KTAS/alt 75 reads the 0.79 drag row (24) at smash 8.3. Form drag grows
    # with speed -- an earlier drag/smash*10 version shrank with it instead.
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)
    state = make_state(adc=adc, weight=17.4, ktas=485.0, altitude=75)

    assert state.get_form_delta_ktas() == pytest.approx(24.0 * 8.3 / 10)

    slower = make_state(adc=adc, weight=17.4, ktas=300.0, altitude=75)
    assert slower.get_form_delta_ktas() < state.get_form_delta_ktas()


def test_calculate_performance_form_delta_ktas_uses_total_drag():
    state = make_state(adc=_make_drag_adc(), ktas=337.3, altitude=0)

    performance = calculate_performance(state, segment_pulls=0, delta_altitude=0)

    expected_form_delta_ktas = state.get_total_drag() * state.get_smash() / 10
    assert performance.form_delta_ktas == pytest.approx(expected_form_delta_ktas)
    assert performance.form_delta_ktas > 0
    # And it actually participates in the resulting speed, not just the
    # reported breakdown (the original bug engine_delta_ktas had before it
    # was wired into new_ktas).
    assert performance.new_state.ktas == pytest.approx(
        state.ktas
        - performance.induced_delta_ktas
        + performance.gravity_delta_ktas
        - performance.form_delta_ktas
        + performance.engine_delta_ktas
    )


# ---------------------------------------------------------------------------
# calculate_performance / afterburner toggle
# ---------------------------------------------------------------------------

def test_calculate_performance_afterburner_defaults_to_true():
    state = make_state(adc=make_ab_adc(), ktas=337.3, altitude=0)

    performance = calculate_performance(state, segment_pulls=0, delta_altitude=0)

    assert performance.afterburner is True
    assert performance.engine_output == pytest.approx(50.0)  # AB chart's value


def test_calculate_performance_afterburner_false_uses_dry_chart():
    state = make_state(adc=make_ab_adc(), ktas=337.3, altitude=0)

    performance = calculate_performance(
        state, segment_pulls=0, delta_altitude=0, afterburner=False
    )

    assert performance.afterburner is False
    assert performance.engine_output == pytest.approx(30.0)  # dry chart's value


def test_calculate_performance_engine_output_clamp_respects_afterburner_toggle():
    # Regression test: max_engine_output used to always call
    # state.get_engine_output() with no argument (always AB-on), so an
    # override would clamp against the AB max even on a dry-only turn --
    # requesting more than the dry engine can do should clamp to the DRY
    # max here, not the (higher) AB max.
    state = make_state(adc=make_ab_adc(), ktas=337.3, altitude=0)

    performance = calculate_performance(
        state, segment_pulls=0, delta_altitude=0, afterburner=False, engine_output=999.0
    )

    assert performance.engine_output == pytest.approx(30.0)  # dry max, not AB's 50.0


def test_turn_performance_max_engine_output_respects_actual_afterburner_used():
    # Regression test: max_engine_output used to be a @property with its own
    # afterburner parameter -- a property can't take arguments from the
    # caller, so that parameter was dead and it always reported the AB max
    # regardless of which mode the turn actually used.
    state = make_state(adc=make_ab_adc(), ktas=337.3, altitude=0)

    dry_turn = calculate_performance(state, segment_pulls=0, afterburner=False)
    assert dry_turn.max_engine_output == pytest.approx(30.0)

    ab_turn = calculate_performance(state, segment_pulls=0, afterburner=True)
    assert ab_turn.max_engine_output == pytest.approx(50.0)


def test_performance_history_resolve_turn_passes_through_afterburner():
    state = make_state(adc=make_ab_adc(), ktas=337.3, altitude=0)
    history = PerformanceHistory(state)

    performance = history.resolve_turn(segment_pulls=0, afterburner=False)

    assert performance.afterburner is False
    assert performance.engine_output == pytest.approx(30.0)


def test_calculate_performance_clamps_segment_fp_to_between_one_and_current_speed_fp():
    state = make_state(ktas=485.0, altitude=75)
    speed = speedbop.speed_fp_from_ktas(state.ktas)

    too_long = calculate_performance(state, segment_pulls=10, segment_fp=speed + 20)
    assert too_long.segment_fp == speed

    too_short = calculate_performance(state, segment_pulls=10, segment_fp=-5)
    assert too_short.segment_fp == 1

    in_range = calculate_performance(state, segment_pulls=10, segment_fp=speed - 1)
    assert in_range.segment_fp == speed - 1


def test_calculate_performance_clamps_delta_altitude_so_altitude_never_goes_negative():
    state = make_state(ktas=485.0, altitude=10)

    performance = calculate_performance(state, segment_pulls=5, delta_altitude=-25)

    assert performance.new_state.altitude == 0
    assert performance.delta_altitude == -10  # clamped to what was actually available


def test_calculate_performance_clamped_altitude_also_affects_the_gravity_term():
    # The gravity term has to reflect the descent that actually happened,
    # not the originally requested delta_altitude -- you can't gain more
    # energy diving than you had altitude to dive through.
    state = make_state(ktas=485.0, altitude=10)
    speed = speedbop.speed_fp_from_ktas(state.ktas)

    performance = calculate_performance(state, segment_pulls=5, delta_altitude=-25)

    assert performance.gravity_delta_ktas == pytest.approx(10.0 / speed * 60)


def test_calculate_performance_does_not_clamp_delta_altitude_when_climbing():
    state = make_state(ktas=485.0, altitude=10)

    performance = calculate_performance(state, segment_pulls=5, delta_altitude=20)

    assert performance.new_state.altitude == 30
    assert performance.delta_altitude == 20


def test_calculate_performance_clamps_segment_pulls_to_max_load():
    state = make_state(ktas=485.0, altitude=75)
    max_load = state.get_max_load()

    performance = calculate_performance(state, segment_pulls=max_load + 50, delta_altitude=0)

    assert performance.segment_pulls == max_load
    assert performance.gs == pytest.approx(gs_from_pulls(max_load))


def test_calculate_performance_does_not_clamp_segment_pulls_under_max_load():
    state = make_state(ktas=485.0, altitude=75)
    max_load = state.get_max_load()

    performance = calculate_performance(state, segment_pulls=max_load - 1, delta_altitude=0)

    assert performance.segment_pulls == max_load - 1


def test_calculate_performance_keeps_adc_and_weight_unchanged():
    state = make_state(ktas=485.0, altitude=75, weight=17.4)

    performance = calculate_performance(state, segment_pulls=22, delta_altitude=-15)

    assert performance.new_state.adc is state.adc
    assert performance.new_state.weight == state.weight


def test_turn_performance_smash_is_the_one_alpha_was_computed_from():
    state = make_state(ktas=485.0, altitude=75)

    performance = calculate_performance(state, segment_pulls=10, delta_altitude=0)

    assert performance.smash == pytest.approx(state.get_smash())
    assert performance.alpha == pytest.approx(10 / performance.smash * state.get_lcs())


def test_turn_performance_gs_and_new_speed_fp():
    state = make_state(ktas=485.0, altitude=75)

    performance = calculate_performance(state, segment_pulls=21, delta_altitude=0)

    assert performance.gs == pytest.approx(7.0)  # 21 pulls / 3
    assert performance.new_speed_fp == speedbop.speed_fp_from_ktas(performance.new_state.ktas)


def test_turn_speed_fp_comes_from_ktas_not_keas():
    # Regression test: the turn breakdown read FP off KEAS while the turn
    # screen's stat bar reads it off KTAS, so at altitude they disagreed
    # (383 KTAS at alt 30 is 346 KEAS: 9 FP vs 10 FP).
    state = make_state(ktas=383.0, altitude=30)
    assert state.get_keas() == 346

    performance = calculate_performance(state, segment_pulls=0, delta_altitude=0)

    assert performance.initial_speed_fp == speedbop.speed_fp_from_ktas(383.0) == 10
    assert performance.new_speed_fp == speedbop.speed_fp_from_ktas(performance.new_state.ktas)


def test_turn_performance_is_frozen():
    state = make_state(ktas=485.0, altitude=75)
    performance = calculate_performance(state, segment_pulls=22, delta_altitude=-15)

    with pytest.raises(dataclasses.FrozenInstanceError):
        performance.segment_pulls = 0


def test_turn_performance_format_includes_key_numbers():
    state = make_state(ktas=485.0, altitude=75)
    performance = calculate_performance(state, segment_pulls=22, delta_altitude=-15)

    text = performance.format()

    assert "[Performance]" in text
    assert "Pulls:         22" in text
    assert "DAlt:          -15" in text
    assert "(max 20.0)" in text  # _make_adc's alpha_max


def test_turn_performance_max_alpha_is_the_aircrafts_alpha_max():
    # Derived from old_state (the turn's starting state), not new_state --
    # alpha_max is a constant of the airframe either way, but this pins
    # which state it's read off in case that ever stops being true.
    state = make_state(ktas=485.0, altitude=75)
    performance = calculate_performance(state, segment_pulls=22, delta_altitude=-15)

    assert performance.max_alpha == performance.old_state.adc.lift.alpha_max


def test_turn_performance_max_engine_output_is_read_off_the_old_state():
    # Unlike max_alpha, this one genuinely could differ between old/new
    # state (the engine chart is indexed by altitude/mach, both of which
    # change turn to turn) -- old_state is what the turn's default/override
    # was actually chosen against, so that's what "max available" means.
    state = make_state(ktas=485.0, altitude=75)
    performance = calculate_performance(state, segment_pulls=22, delta_altitude=-15)

    assert performance.max_engine_output == pytest.approx(performance.old_state.get_engine_output())
    assert "Engine  dKTAS:" in performance.format()
    assert f"max {performance.max_engine_output}," in performance.format()


# ---------------------------------------------------------------------------
# PerformanceHistory
# ---------------------------------------------------------------------------

def test_performance_history_current_state_starts_as_initial_state():
    state = make_state(ktas=485.0, altitude=75)
    history = PerformanceHistory(state)

    assert history.current_state is state
    assert history.turns == []


def test_performance_history_resolve_turn_chains_state_automatically():
    # The whole point: a caller sets state once and calls resolve_turn()
    # repeatedly without manually threading the returned state back in.
    state = make_state(ktas=485.0, altitude=75)
    history = PerformanceHistory(state)

    p1 = history.resolve_turn(segment_pulls=22, delta_altitude=-15)
    assert history.current_state is p1.new_state
    assert history.current_state.altitude == 60

    p2 = history.resolve_turn(segment_pulls=10, delta_altitude=5)
    assert history.current_state is p2.new_state
    assert history.current_state.altitude == 65
    # the second turn's inputs were the FIRST turn's result, not the
    # original state -- confirms chaining, not independent calls
    assert p2.old_state is p1.new_state

    assert history.turns == [p1, p2]


def test_performance_history_resolve_turn_passes_through_engine_output_override():
    state = make_state(ktas=485.0, altitude=75)
    history = PerformanceHistory(state)

    performance = history.resolve_turn(segment_pulls=0, delta_altitude=0, engine_output=5.0)

    assert performance.engine_output == pytest.approx(5.0)
    assert history.current_state.ktas == pytest.approx(
        state.ktas + performance.engine_delta_ktas
    )


def test_performance_history_format_joins_every_turn():
    state = make_state(ktas=485.0, altitude=75)
    history = PerformanceHistory(state)
    history.resolve_turn(segment_pulls=22, delta_altitude=-15)
    history.resolve_turn(segment_pulls=10, delta_altitude=5)

    assert history.format().count("[Performance]") == 2


# ---------------------------------------------------------------------------
# Inverse helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def test_main_runs_and_prints_expected_values(capsys, monkeypatch):
    # main() hardcodes "adc/swift-mk5.json" relative to the CWD, not to this
    # file, so it only resolves correctly when run from the repo root.
    monkeypatch.chdir(REPO_ROOT)

    speedbop.main()

    out = capsys.readouterr().out
    assert "Wing load:  52.7" in out
    assert "Safe load:  20.9" in out
    assert "KTAS: 385 10" in out
    assert "KEAS: 342" in out
    assert "Q: 39.6" in out
    assert "Smash: 7.5" in out
    assert "Mach: 0.6" in out
    assert "Engine output: 57.1" in out
    assert "LCS: 5.6" in out
    assert "Max load: 34" in out
    assert "Corner speed: 279" in out
    assert "[Performance]" in out
    assert "Pulls:         22" in out
    # Swift Mk5 has an afterburner, resolve_turn() defaults afterburner=True,
    # and main() doesn't override engine_output, so the turn uses the AB
    # chart's max (57.1) -- divided by the engine scale into 56.2 base
    # knots, then scaled by combat weight / weight (15.8/17.4) to 51.0.
    assert "Engine  dKTAS: 51.0 (output 57.1, max 57.1, base 56.2)" in out
    # Form drag is no longer hardcoded to 0 -- Swift Mk5's form table
    # produces a nonzero value at this state's mach.
    assert "Form    dKTAS: 18.8" in out

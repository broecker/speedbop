import dataclasses
import json
import math
import pathlib

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
    keas_from_q,
    ktas_from_keas,
    ktas_from_q,
    q_from_smash,
)
from chart import Isobar, IsobarChart, load_isobars

REPO_ROOT = pathlib.Path(__file__).parent.parent
REAL_ADC_PATH = REPO_ROOT / "adc" / "fj-3m.json"


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

def _make_adc(**overrides):
    defaults = dict(
        name="Test Plane",
        version="1.0",
        lift=AircraftDataCard.Lift(
            alpha_max=20.0,
            mach_lcs_ids_table={0.5: (4.0, 100), 1.0: (2.0, 50)},
        ),
        characteristics=AircraftDataCard.Characteristics(wing_area=2.0, combat_safe_load=10.0),
        stores=AircraftDataCard.Stores(combat_weight=5.0),
        dry_engine_output=IsobarChart([
            Isobar(output=30.0, altitude=[0.0, 100.0], mach=[0.3, 0.9]),
            Isobar(output=60.0, altitude=[0.0, 100.0], mach=[0.1, 0.5]),
        ]),
    )
    defaults.update(overrides)
    return AircraftDataCard(**defaults)


def _make_state(**overrides):
    defaults = dict(adc=_make_adc(), weight=17.4, ktas=100.0, altitude=0)
    defaults.update(overrides)
    return AircraftState(**defaults)


def test_aircraft_data_card_is_frozen():
    adc = _make_adc()

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
    assert isinstance(adc.stores, AircraftDataCard.Stores)
    assert adc.stores.combat_weight == 8.5
    assert isinstance(adc.dry_engine_output, IsobarChart)
    assert adc.dry_engine_output.interpolate(altitude=0, mach=0.5) == pytest.approx(10.0)


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
    adc = _make_adc(characteristics=AircraftDataCard.Characteristics(
        wing_area=3.0, combat_safe_load=21.0,
    ))
    state = _make_state(adc=adc, weight=17.4)

    assert state.get_wing_load() == pytest.approx(round(17.4 / 3.0 * 10.0, 1))


def test_get_safe_load():
    adc = _make_adc(
        characteristics=AircraftDataCard.Characteristics(wing_area=3.0, combat_safe_load=21.0),
        stores=AircraftDataCard.Stores(combat_weight=15.7),
    )
    state = _make_state(adc=adc, weight=17.4)

    assert state.get_safe_load() == pytest.approx(round(15.7 / 17.4 * 21.0, 1))


def test_get_keas_at_sea_level_equals_ktas():
    # KEAS == KTAS at altitude 0 by definition (exp(0.003358*0) == 1) --
    # the same boundary condition e6b/samples/keas.csv was fit against.
    state = _make_state(ktas=123.0, altitude=0)

    assert state.get_keas() == 123


def test_get_keas_applies_altitude_correction_and_rounds():
    state = _make_state(ktas=285.0, altitude=75)

    # keas = round(285 / exp(0.003358*75)) -- rounds because KEAS is read
    # off the device as a whole number, same granularity as e6b/keas.csv.
    assert state.get_keas() == round(285.0 / math.exp(0.003358 * 75))
    assert isinstance(state.get_keas(), int)


def test_get_q_uses_rounded_keas():
    # q = round(keas^2 / 2950, 1), using the rounded get_keas() result --
    # not the raw unrounded ratio -- so this pins the two methods'
    # composition, not just the formula in isolation.
    state = _make_state(ktas=100.0, altitude=0)  # keas rounds to exactly 100

    assert state.get_q() == pytest.approx(round(100**2 / 2950, 1))


def test_get_smash_uses_wing_load_not_a_raw_weight():
    # smash = round(10 * q / get_wing_load(), 1) -- confirms it's composed
    # from the *wing load* value (weight/wing_area*10) and the already-
    # rounded get_q(), not raw aircraft weight or an unrounded q, matching
    # the "wl" variable e6b/smash.csv was fit against.
    adc = _make_adc(characteristics=AircraftDataCard.Characteristics(
        wing_area=3.0, combat_safe_load=21.0,
    ))
    state = _make_state(adc=adc, weight=17.4, ktas=100.0, altitude=0)

    expected_q = round(100**2 / 2950, 1)
    expected_wing_load = 17.4 / 3.0 * 10.0
    assert state.get_smash() == pytest.approx(round(10.0 * expected_q / expected_wing_load, 1))


def test_get_mach_is_the_inverse_of_the_keas_formula():
    # keas = 674.6 * mach * exp(-0.0045*altitude), so mach = keas *
    # exp(0.0045*altitude) / 674.6 -- and at altitude=0 that's just
    # keas/674.6, the cleanest case to pin down independent of the
    # exponential term.
    state = _make_state(ktas=674.6, altitude=0)  # keas rounds to 675

    assert state.get_mach() == pytest.approx(round(675 / 674.6, 1))


def test_get_mach_uses_rounded_keas_and_applies_altitude_correction():
    # Cross-checks against a real e6b/mach.csv reading (keas=250, alt=225,
    # mach=1.0) -- ktas is chosen so get_keas() rounds to exactly 250.
    state = _make_state(ktas=250 * math.exp(0.003358 * 225), altitude=225)

    assert state.get_keas() == 250
    assert state.get_mach() == pytest.approx(1.0, abs=0.02)


def test_get_lcs_looks_up_by_mach():
    # ktas/altitude chosen so get_mach() lands exactly on 0.5, which is a
    # key in the synthetic table -- _bop_tablerow_lookup returns that
    # entry directly, and get_lcs() takes its first element.
    state = _make_state(ktas=337.3, altitude=0)

    assert state.get_mach() == pytest.approx(0.5)
    assert state.get_lcs() == pytest.approx(4.0)


def test_get_ids_looks_up_by_mach():
    # Same lookup as get_lcs(), but takes the table row's second element.
    state = _make_state(ktas=337.3, altitude=0)

    assert state.get_mach() == pytest.approx(0.5)
    assert state.get_ids() == pytest.approx(100)


def test_get_engine_output_matches_the_isobar_chart_directly():
    # get_engine_output() should be a thin, rounded wrapper around the
    # chart embedded in the aircraft's own data card.
    state = _make_state(ktas=337.3, altitude=0)

    expected = round(
        state.adc.dry_engine_output.interpolate(altitude=state.altitude, mach=state.get_mach()), 1
    )
    assert state.get_engine_output() == pytest.approx(expected)


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
    state = _make_state(adc=adc, weight=17.4, ktas=485.0, altitude=75)

    assert state.get_wing_load() == pytest.approx(58.0)
    assert state.get_safe_load() == pytest.approx(18.9)
    assert state.get_keas() == 377
    expected_q = round(377**2 / 2950, 1)
    assert state.get_q() == pytest.approx(expected_q)
    assert state.get_smash() == pytest.approx(round(10.0 * expected_q / 58.0, 1))
    assert speedbop.speed_fp_from_ktas(state.ktas) == 12
    assert state.get_mach() == pytest.approx(0.8)
    assert state.get_engine_output() == pytest.approx(45.9)
    assert state.get_lcs() == pytest.approx(3.8)
    assert state.get_max_load() == 48
    assert state.calculate_corner_speed() == 246


# ---------------------------------------------------------------------------
# calculate_performance / TurnPerformance
# ---------------------------------------------------------------------------

def test_calculate_performance_does_not_mutate_the_input_state():
    # Regression test: calculate_performance used to do `new_state = state;
    # new_state.ktas = new_ktas`, which is aliasing, not a copy -- it
    # silently mutated the caller's original state object too.
    state = _make_state(ktas=485.0, altitude=75)
    original_ktas, original_altitude = state.ktas, state.altitude

    calculate_performance(state, segment_pulls=22, delta_altitude=-15)

    assert state.ktas == original_ktas
    assert state.altitude == original_altitude


def test_calculate_performance_updates_altitude_by_delta():
    # Regression test: delta_altitude used to only affect the gravity
    # term, never actually advancing state.altitude itself.
    state = _make_state(ktas=485.0, altitude=75)

    performance = calculate_performance(state, segment_pulls=22, delta_altitude=-15)

    assert performance.new_state.altitude == 75 - 15


def test_calculate_performance_new_ktas_matches_the_reported_breakdown():
    state = _make_state(ktas=485.0, altitude=75)

    performance = calculate_performance(state, segment_pulls=22, delta_altitude=-15)

    expected_new_ktas = (
        state.ktas
        - performance.induced_delta_ktas
        + performance.gravity_delta_ktas
        - performance.form_delta_ktas
    )
    assert performance.new_state.ktas == pytest.approx(expected_new_ktas)


def test_calculate_performance_clamps_delta_altitude_so_altitude_never_goes_negative():
    state = _make_state(ktas=485.0, altitude=10)

    performance = calculate_performance(state, segment_pulls=5, delta_altitude=-25)

    assert performance.new_state.altitude == 0
    assert performance.delta_altitude == -10  # clamped to what was actually available


def test_calculate_performance_clamped_altitude_also_affects_the_gravity_term():
    # The gravity term has to reflect the descent that actually happened,
    # not the originally requested delta_altitude -- you can't gain more
    # energy diving than you had altitude to dive through.
    state = _make_state(ktas=485.0, altitude=10)
    speed = speedbop.speed_fp_from_ktas(state.get_keas())

    performance = calculate_performance(state, segment_pulls=5, delta_altitude=-25)

    assert performance.gravity_delta_ktas == pytest.approx(10.0 / speed * 60)


def test_calculate_performance_does_not_clamp_delta_altitude_when_climbing():
    state = _make_state(ktas=485.0, altitude=10)

    performance = calculate_performance(state, segment_pulls=5, delta_altitude=20)

    assert performance.new_state.altitude == 30
    assert performance.delta_altitude == 20


def test_calculate_performance_clamps_segment_pulls_to_max_load():
    state = _make_state(ktas=485.0, altitude=75)
    max_load = state.get_max_load()

    performance = calculate_performance(state, segment_pulls=max_load + 50, delta_altitude=0)

    assert performance.segment_pulls == max_load
    assert performance.gs == pytest.approx(gs_from_pulls(max_load))


def test_calculate_performance_does_not_clamp_segment_pulls_under_max_load():
    state = _make_state(ktas=485.0, altitude=75)
    max_load = state.get_max_load()

    performance = calculate_performance(state, segment_pulls=max_load - 1, delta_altitude=0)

    assert performance.segment_pulls == max_load - 1


def test_calculate_performance_keeps_adc_and_weight_unchanged():
    state = _make_state(ktas=485.0, altitude=75, weight=17.4)

    performance = calculate_performance(state, segment_pulls=22, delta_altitude=-15)

    assert performance.new_state.adc is state.adc
    assert performance.new_state.weight == state.weight


def test_turn_performance_gs_and_new_speed_fp():
    state = _make_state(ktas=485.0, altitude=75)

    performance = calculate_performance(state, segment_pulls=21, delta_altitude=0)

    assert performance.gs == pytest.approx(7.0)  # 21 pulls / 3
    assert performance.new_speed_fp == speedbop.speed_fp_from_ktas(performance.new_state.get_keas())


def test_turn_performance_is_frozen():
    state = _make_state(ktas=485.0, altitude=75)
    performance = calculate_performance(state, segment_pulls=22, delta_altitude=-15)

    with pytest.raises(dataclasses.FrozenInstanceError):
        performance.segment_pulls = 0


def test_turn_performance_format_includes_key_numbers():
    state = _make_state(ktas=485.0, altitude=75)
    performance = calculate_performance(state, segment_pulls=22, delta_altitude=-15)

    text = performance.format()

    assert "[Performance]" in text
    assert "Pulls:         22" in text
    assert "DAlt:          -15" in text


# ---------------------------------------------------------------------------
# PerformanceHistory
# ---------------------------------------------------------------------------

def test_performance_history_current_state_starts_as_initial_state():
    state = _make_state(ktas=485.0, altitude=75)
    history = PerformanceHistory(state)

    assert history.current_state is state
    assert history.turns == []


def test_performance_history_resolve_turn_chains_state_automatically():
    # The whole point: a caller sets state once and calls resolve_turn()
    # repeatedly without manually threading the returned state back in.
    state = _make_state(ktas=485.0, altitude=75)
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


def test_performance_history_format_joins_every_turn():
    state = _make_state(ktas=485.0, altitude=75)
    history = PerformanceHistory(state)
    history.resolve_turn(segment_pulls=22, delta_altitude=-15)
    history.resolve_turn(segment_pulls=10, delta_altitude=5)

    assert history.format().count("[Performance]") == 2


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


def test_ktas_from_keas_inverts_get_keas_formula():
    # get_keas(): keas = round(ktas / exp(0.003358*altitude))
    assert ktas_from_keas(keas=200.0, altitude=75) == pytest.approx(
        200.0 * math.exp(0.003358 * 75)
    )


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
    state = _make_state(adc=adc, weight=17.4, ktas=485.0, altitude=75)

    q = state.get_q()
    smash = state.get_smash()
    wing_load = state.get_wing_load()

    assert q_from_smash(smash, wing_load) == pytest.approx(q, abs=0.3)
    assert keas_from_q(q) == pytest.approx(state.get_keas(), abs=1.0)
    assert ktas_from_q(q, state.altitude) == pytest.approx(state.ktas, rel=0.02)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def test_main_runs_and_prints_expected_values(capsys, monkeypatch):
    # main() hardcodes "adc/fj-3m.json" relative to the CWD, not to this
    # file, so it only resolves correctly when run from the repo root.
    monkeypatch.chdir(REPO_ROOT)

    speedbop.main()

    out = capsys.readouterr().out
    assert "Wing load:  58.0" in out
    assert "Safe load:  18.9" in out
    assert "KTAS: 485 12" in out
    assert "KEAS: 377" in out
    assert "Q: 48.2" in out
    assert "Smash: 8.3" in out
    assert "Mach: 0.8" in out
    assert "Engine output: 45.9" in out
    assert "LCS: 3.8" in out
    assert "Max load: 48" in out
    assert "Corner speed: 246" in out
    assert "[Performance]" in out
    assert "Pulls:         22" in out

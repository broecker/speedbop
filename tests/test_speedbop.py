import dataclasses
import json
import math
import pathlib

import pytest

import speedbop
from speedbop import AircraftDataCard, AircraftState, _dataclass_from_dict

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


def test_dataclass_from_dict_silently_skips_conversion_on_extra_key():
    # A key not in the dataclass's fields makes fieldtypes[f] raise KeyError
    # -- caught by the bare `except:`, so the WHOLE object silently falls
    # back to the raw dict instead of raising or ignoring just that key.
    @dataclasses.dataclass(frozen=True)
    class Simple:
        x: float

    d = {"x": 1.0, "unexpected_extra_key": 2.0}

    result = _dataclass_from_dict(Simple, d)

    assert result is d
    assert not isinstance(result, Simple)


def test_dataclass_from_dict_silently_skips_conversion_on_missing_key():
    # A missing required field makes klass(**{...}) raise TypeError --
    # also caught by the bare `except:`, same silent fallback as above.
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
# AircraftDataCard
# ---------------------------------------------------------------------------

def _make_adc(**overrides):
    defaults = dict(
        name="Test Plane",
        version="1.0",
        characteristics=AircraftDataCard.Characteristics(wing_area=2.0, combat_safe_load=10.0),
        stores=AircraftDataCard.Stores(combat_weight=5.0),
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
    path = tmp_path / "plane.json"
    path.write_text(json.dumps({
        "name": "Test Plane",
        "version": "2.3",
        "characteristics": {"wing_area": 4.0, "combat_safe_load": 12.0},
        "stores": {"combat_weight": 8.5},
    }))

    adc = AircraftDataCard.from_json(path)

    assert adc.name == "Test Plane"
    assert adc.version == "2.3"
    assert isinstance(adc.characteristics, AircraftDataCard.Characteristics)
    assert adc.characteristics.wing_area == 4.0
    assert adc.characteristics.combat_safe_load == 12.0
    assert isinstance(adc.stores, AircraftDataCard.Stores)
    assert adc.stores.combat_weight == 8.5


def test_aircraft_data_card_from_json_reads_real_fixture():
    # Regression test against the actual repo fixture used by main().
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)

    assert adc.name == "FJ-3M Fury"
    assert adc.version == "1.25.01"
    assert adc.characteristics.wing_area == pytest.approx(3.0)
    assert adc.characteristics.combat_safe_load == pytest.approx(21)
    assert adc.stores.combat_weight == pytest.approx(15.7)


def test_aircraft_data_card_from_dict_does_not_build_nested_dataclasses():
    # _from_dict is currently unused dead code (from_json calls
    # _dataclass_from_dict directly; the cls._from_dict(...) call is
    # commented out) -- and for good reason: unlike from_json, it does NOT
    # recursively convert nested fields. characteristics/stores end up as
    # plain dicts, not AircraftDataCard.Characteristics/.Stores instances.
    data = {
        "name": "Test Plane",
        "version": "1.0",
        "characteristics": {"wing_area": 4.0, "combat_safe_load": 12.0},
        "stores": {"combat_weight": 8.5},
    }

    adc = AircraftDataCard._from_dict(data)

    assert adc.name == "Test Plane"
    assert isinstance(adc.characteristics, dict)
    assert not isinstance(adc.characteristics, AircraftDataCard.Characteristics)


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
    # keas = 674.573 * mach * exp(-0.0044558*altitude), so mach = keas *
    # exp(0.0044558*altitude) / 674.573 -- and at altitude=0 that's just
    # keas/674.573, the cleanest case to pin down independent of the
    # exponential term.
    state = _make_state(ktas=674.573, altitude=0)  # keas rounds to 675

    assert state.get_mach() == pytest.approx(round(675 / 674.573, 1))


def test_get_mach_uses_rounded_keas_and_applies_altitude_correction():
    # Cross-checks against a real e6b/mach.csv reading (keas=250, alt=225,
    # mach=1.0) -- ktas is chosen so get_keas() rounds to exactly 250.
    state = _make_state(ktas=250 * math.exp(0.003358 * 225), altitude=225)

    assert state.get_keas() == 250
    assert state.get_mach() == pytest.approx(1.0, abs=0.02)


@pytest.mark.parametrize("ktas,expected", [
    (0.0, 1),
    (59.9, 1),
    (60.0, 2),
    (99.0, 2),
    (100.0, 3),
    (139.0, 3),
    (140.0, 4),
])
def test_get_speed_steps_every_40_knots_above_60(ktas, expected):
    state = _make_state(ktas=ktas)

    assert state.get_speed() == expected


def test_aircraft_state_against_real_fixture():
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)
    state = _make_state(adc=adc, weight=17.4, ktas=285.0, altitude=75)

    assert state.get_wing_load() == pytest.approx(58.0)
    assert state.get_safe_load() == pytest.approx(18.9)
    assert state.get_keas() == 222
    expected_q = round(222**2 / 2950, 1)
    assert state.get_q() == pytest.approx(expected_q)
    assert state.get_smash() == pytest.approx(round(10.0 * expected_q / 58.0, 1))
    assert state.get_speed() == 7
    assert state.get_mach() == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def test_main_runs_and_prints_expected_values(capsys, monkeypatch):
    # main() hardcodes "adc/fj-3m.json" relative to the CWD, not to this
    # file, so it only resolves correctly when run from the repo root.
    monkeypatch.chdir(REPO_ROOT)

    speedbop.main()

    out = capsys.readouterr().out
    assert "Hello Speedbop!" in out
    assert "58.0" in out
    assert "Safe load:  18.9" in out
    assert "KTAS: 285 7" in out
    assert "KEAS: 222" in out
    assert "Q: 16.7" in out
    assert "Smash: 2.9" in out
    assert "Mach: 0.5" in out

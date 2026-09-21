import dataclasses
import json
import pathlib

import pytest

import speedbop
from speedbop import AircraftDataCard, AircraftState, dataclass_from_dict

REPO_ROOT = pathlib.Path(__file__).parent.parent
REAL_ADC_PATH = REPO_ROOT / "adc" / "fj-3m.json"


# ---------------------------------------------------------------------------
# dataclass_from_dict
# ---------------------------------------------------------------------------

def test_dataclass_from_dict_passes_scalars_through_unchanged():
    # klass isn't a dataclass, so dataclasses.fields(klass) raises and the
    # bare except falls back to returning the raw value as-is.
    assert dataclass_from_dict(str, "hello") == "hello"
    assert dataclass_from_dict(float, 3.0) == 3.0


def test_dataclass_from_dict_builds_nested_dataclasses():
    @dataclasses.dataclass(frozen=True)
    class Inner:
        x: float

    @dataclasses.dataclass(frozen=True)
    class Outer:
        name: str
        inner: Inner

    result = dataclass_from_dict(Outer, {"name": "a", "inner": {"x": 1.5}})

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

    result = dataclass_from_dict(Simple, d)

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

    result = dataclass_from_dict(Simple, d)

    assert result is d
    assert not isinstance(result, Simple)


def test_dataclass_from_dict_list_branch_has_a_name_error_bug():
    # BUG (speedbop.py, dataclass_from_dict): the list branch iterates over
    # `data`, but the function's parameter is named `d` -- `data` is never
    # defined, so any list-typed field raises NameError instead of
    # converting. This test documents the current (broken) behavior rather
    # than silently working around it.
    with pytest.raises(NameError):
        dataclass_from_dict(list[str], ["a", "b"])


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
    # dataclass_from_dict directly; the cls._from_dict(...) call is
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
    state = AircraftState(adc, weight=17.4)

    assert state.get_wing_load() == pytest.approx(17.4 / 3.0 * 10.0)


def test_get_safe_load():
    adc = _make_adc(
        characteristics=AircraftDataCard.Characteristics(wing_area=3.0, combat_safe_load=21.0),
        stores=AircraftDataCard.Stores(combat_weight=15.7),
    )
    state = AircraftState(adc, weight=17.4)

    assert state.get_safe_load() == pytest.approx(15.7 / 17.4 * 21.0)


def test_aircraft_state_against_real_fixture():
    adc = AircraftDataCard.from_json(REAL_ADC_PATH)
    state = AircraftState(adc, weight=17.4)

    assert state.get_wing_load() == pytest.approx(58.0)
    assert state.get_safe_load() == pytest.approx(18.9482758620, rel=1e-9)


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
    assert "18.94827586" in out

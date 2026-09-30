"""Shared test fixtures: small synthetic aircraft data cards and states."""

import pathlib

from chart import Isobar, IsobarChart
from speedbop import AircraftDataCard, AircraftState

REPO_ROOT = pathlib.Path(__file__).parent.parent
REAL_ADC_PATH = REPO_ROOT / "adc" / "fj-3m.json"


def make_adc(**overrides):
    defaults = dict(
        name="Test Plane",
        version="1.0",
        lift=AircraftDataCard.Lift(
            alpha_max=20.0,
            mach_lcs_ids_table={0.5: (4.0, 100), 1.0: (2.0, 50)},
        ),
        characteristics=AircraftDataCard.Characteristics(wing_area=2.0, combat_safe_load=10.0),
        # A single entry always resolves to drag=0 regardless of query mach
        # (_bop_tablerow_lookup falls back to it either as the matching
        # ceiling entry or the highest-key fallback) -- so every existing
        # test that doesn't care about form drag keeps its zero-form
        # assumption; tests that do care override this explicitly.
        form=AircraftDataCard.Form(brake=0, mach_to_drag_table={0.5: 0}),
        roll_rate={0.5: "Slow", 1.0: "Fast"},
        stores=AircraftDataCard.Stores(combat_weight=5.0),
        dry_engine_output=IsobarChart([
            Isobar(output=30.0, altitude=[0.0, 100.0], mach=[0.3, 0.9]),
            Isobar(output=60.0, altitude=[0.0, 100.0], mach=[0.1, 0.5]),
        ]),
        ab_engine_output=None,
    )
    defaults.update(overrides)
    return AircraftDataCard(**defaults)


def make_state(**overrides):
    defaults = dict(adc=make_adc(), weight=17.4, ktas=100.0, altitude=0)
    defaults.update(overrides)
    return AircraftState(**defaults)


def make_ab_adc(**overrides):
    # Deliberately different values from make_adc()'s dry chart at the
    # same (altitude, mach), so AB-vs-dry selection is unambiguous in tests.
    ab_chart = IsobarChart([
        Isobar(output=50.0, altitude=[0.0, 100.0], mach=[0.3, 0.9]),
        Isobar(output=90.0, altitude=[0.0, 100.0], mach=[0.1, 0.5]),
    ])
    return make_adc(ab_engine_output=ab_chart, **overrides)

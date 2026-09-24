import dataclasses
from dataclasses import dataclass, asdict, fields
import json
import math
import pathlib

from chart import IsobarChart, load_isobars


# https://stackoverflow.com/a/54769644
def _dataclass_from_dict(klass, d):
    if isinstance(d, list):
        (inner,) = klass.__args__
        return [_dataclass_from_dict(inner, i) for i in data]

    try:
        fieldtypes = {f.name: f.type for f in dataclasses.fields(klass)}
        return klass(
            **{
                f: _dataclass_from_dict(fieldtypes[f], d[f])
                for f in d
                if f in fieldtypes
            }
        )
    except:
        return d  # Not a dataclass field


def _bop_tablerow_lookup(value: float, table: dict[str, any]) -> any:
    # We need to check for str here, as json only accepts strings, not numbers
    # as dict keys.
    all_keys = sorted(table.keys())
    for key in all_keys:
        val = float(key)
        if val >= value:
            return table[key]
    return table[all_keys[-1]]


@dataclass(frozen=True)
class AircraftDataCard:
    # Represents a single type of airplane. All of these fields are constant.
    name: str
    version: str

    @dataclass(frozen=True)
    class Characteristics:
        wing_area: float

        # In loads; i.e. 1/3 g's
        combat_safe_load: float

    @dataclass(frozen=True)
    class Form:
        brake: int
        mach_to_drag_table: dict[float, int]

    @dataclass(frozen=True)
    class Stores:
        combat_weight: float

    @dataclass(frozen=True)
    class Lift:
        alpha_max: float
        mach_lcs_ids_table: dict[float, tuple[float, int]]

    characteristics: Characteristics
    form: Form
    lift: Lift
    stores: Stores

    dry_engine_output: IsobarChart

    @classmethod
    def from_json(cls, path: pathlib.Path) -> "AircraftDataCard":
        with open(path, "r") as file:
            adc_dict = json.loads(file.read())

            # Let's replace the relative path to the engine chart with an actual chart
            # instance.
            if adc_dict["dry_engine_output"]:
                chart_file = path.parent / adc_dict["dry_engine_output"]
                adc_dict["dry_engine_output"] = IsobarChart(load_isobars(chart_file))

            return _dataclass_from_dict(AircraftDataCard, adc_dict)


@dataclass
class AircraftState:
    adc: AircraftDataCard

    # From scenario.
    weight: float

    # From scenario / last turn.
    ktas: float
    altitude: int

    def get_wing_load(self) -> float:
        return round(self.weight / self.adc.characteristics.wing_area * 10.0, 1)

    def get_safe_load(self) -> float:
        safe_load = (
            self.adc.stores.combat_weight
            / self.weight
            * self.adc.characteristics.combat_safe_load
        )
        return round(safe_load, 1)

    def get_keas(self) -> float:
        return round(self.ktas / math.exp(0.003358 * self.altitude))

    def get_q(self) -> float:
        keas = self.get_keas()
        # q = keas² / 2950 (or keas² * 0.000339); empirically determined.
        return round(keas**2 / 2950, 1)

    def get_smash(self) -> float:
        return round(10.0 * self.get_q() / self.get_wing_load(), 1)

    def get_mach(self) -> float:
        keas = self.get_keas()
        # keas = 674.6 * mach * exp(-0.0045 * altitude); empirically determined.
        mach = keas * math.exp(0.0045 * self.altitude) / 674.6
        return round(mach, 1)

    def get_engine_output(self) -> float:
        chart = self.adc.dry_engine_output
        return round(chart.interpolate(altitude=self.altitude, mach=self.get_mach()), 1)

    def get_form_drag(self) -> float:
        mach = self.get_mach()
        return float(_bop_tablerow_lookup(mach, self.adc.form.mach_to_drag_table))

    def get_lcs(self) -> float:
        mach = self.get_mach()
        lcs = _bop_tablerow_lookup(mach, self.adc.lift.mach_lcs_ids_table)
        return lcs[0]

    def get_ids(self) -> float:
        mach = self.get_mach()
        row = _bop_tablerow_lookup(mach, self.adc.lift.mach_lcs_ids_table)
        return row[1]

    def get_max_load(self) -> int:
        max_load = self.adc.lift.alpha_max / self.get_lcs() * self.get_smash()
        # We don't want to exceed our max load ever, hence we round down.
        return math.floor(max_load)

    def calculate_corner_speed(self) -> float:
        alpha_over_lcs = self.adc.lift.alpha_max / self.get_lcs()
        desired_smash = self.adc.characteristics.combat_safe_load / alpha_over_lcs
        desired_q = q_from_smash(desired_smash, self.get_wing_load())
        desired_keas = keas_from_q(desired_q)
        return math.floor(desired_keas)

    def get_total_drag(self) -> float:
        form_drag = self.get_form_drag()
        # TODO(mbroecker): Enable through toggle.
        brake_drag = 0.0
        stores_drag = 0.0
        return form_drag + brake_drag + stores_drag


def speed_fp_from_ktas(ktas: float) -> int:
    if ktas < 60:
        return 1
    return 2 + (ktas - 60) // 40


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


def gs_from_pulls(pulls: int) -> float:
    return round(pulls / 3, 1)


@dataclass(frozen=True)
class TurnPerformance:
    """The result of resolving one turn's movement via calculate_performance().

    Captures every intermediate value that used to only exist as a print()
    statement, so a caller (a web UI, a turn history log) can inspect the
    breakdown instead of just the final state.
    """

    segment_pulls: int
    segment_fp: int
    initial_speed_fp: int
    delta_altitude: int

    alpha: float
    induced_delta_ktas: float
    gravity_delta_ktas: float
    form_delta_ktas: float
    engine_delta_ktas: float

    old_state: AircraftState
    new_state: AircraftState

    @property
    def gs(self) -> float:
        return gs_from_pulls(self.segment_pulls)

    @property
    def new_speed_fp(self) -> int:
        return speed_fp_from_ktas(self.new_state.get_keas())

    @property
    def max_alpha(self) -> float:
        return self.old_state.adc.lift.alpha_max

    @property
    def max_engine_output(self) -> float:
        return self.old_state.get_engine_output()

    def format(self) -> str:
        lines = [
            "-" * 79,
            "[Performance]",
            f"Pulls:         {self.segment_pulls} ( {self.gs} Gs)",
            f"Segment length {self.segment_fp} / {self.initial_speed_fp}",
            f"DAlt:          {self.delta_altitude}",
            f"Alpha:         {round(self.alpha, 1)} (max {self.max_alpha})",
            f"Induced dKTAS: {round(self.induced_delta_ktas, 1)}",
            f"Grav    dKTAS: {round(self.gravity_delta_ktas, 1)}",
            f"Form    dKTAS: {round(self.form_delta_ktas, 1)}",
            f"Engine  dKTAS: {round(self.engine_delta_ktas, 1)} (max {self.max_engine_output})",
            f" => New speed: {round(self.new_state.ktas, 0)} ( {self.new_speed_fp} FP)",
            "-" * 79,
        ]
        return "\n".join(lines)


def calculate_performance(
    state: AircraftState,
    segment_pulls: int,
    segment_fp: int | None = None,
    delta_altitude: int = 0,
    engine_output: float | None = None,
) -> TurnPerformance:
    # Structural load and sea level are hard limits, not suggestions -- clamp
    # here so every caller gets them for free, not just ones that also apply
    # the UI's own stepper limits.
    segment_pulls = min(segment_pulls, state.get_max_load())
    delta_altitude = max(delta_altitude, -state.altitude)

    speed = speed_fp_from_ktas(state.get_keas())
    if segment_fp:
        # A segment can't be longer than the FP your current speed actually
        # allows, and a segment of length <= 0 makes the load division below
        # meaningless -- clamp to [1, speed] rather than trusting the input.
        segment_fp = max(1, min(segment_fp, speed))
        load = float(segment_pulls) / segment_fp * speed
    else:
        segment_fp = speed
        load = float(segment_pulls)

    alpha = load / state.get_smash() * state.get_lcs()

    dl = alpha / state.get_ids() * load * 100
    induced_delta_ktas = dl / speed * segment_fp

    gravity_delta_ktas = float(delta_altitude) / speed * 60 * -1
    form_delta_ktas = state.get_total_drag() / state.get_smash() * 10

    # A caller may set this explicitly (e.g. a throttle setting other than max).
    max_engine_output = state.get_engine_output()
    engine_delta_ktas = (
        max_engine_output
        if engine_output is None
        else min(engine_output, max_engine_output)
    )

    new_ktas = (
        state.ktas
        - induced_delta_ktas
        + gravity_delta_ktas
        - form_delta_ktas
        + engine_delta_ktas
    )
    new_altitude = state.altitude + delta_altitude
    new_state = dataclasses.replace(state, ktas=new_ktas, altitude=new_altitude)

    return TurnPerformance(
        segment_pulls=segment_pulls,
        segment_fp=segment_fp,
        initial_speed_fp=speed,
        delta_altitude=delta_altitude,
        alpha=alpha,
        induced_delta_ktas=induced_delta_ktas,
        gravity_delta_ktas=gravity_delta_ktas,
        form_delta_ktas=form_delta_ktas,
        engine_delta_ktas=engine_delta_ktas,
        old_state=state,
        new_state=new_state,
    )


@dataclass
class PerformanceHistory:
    """Tracks an aircraft's state turn by turn.

    Set up once with the starting state, then call resolve_turn()
    repeatedly -- it threads state through calculate_performance()
    automatically, so a caller (e.g. a web UI) never has to pass the
    updated state back in manually.
    """

    initial_state: AircraftState
    turns: list[TurnPerformance] = dataclasses.field(default_factory=list)

    @property
    def current_state(self) -> AircraftState:
        return self.turns[-1].new_state if self.turns else self.initial_state

    def resolve_turn(
        self,
        segment_pulls: int,
        segment_fp: int | None = None,
        delta_altitude: int = 0,
        engine_output: float | None = None,
    ) -> TurnPerformance:
        performance = calculate_performance(
            self.current_state,
            segment_pulls,
            segment_fp=segment_fp,
            delta_altitude=delta_altitude,
            engine_output=engine_output,
        )
        self.turns.append(performance)
        return performance

    def format(self) -> str:
        return "\n".join(turn.format() for turn in self.turns)


def main() -> None:
    adc = AircraftDataCard.from_json(pathlib.Path("adc/fj-3m.json"))
    state = AircraftState(adc, weight=17.4, ktas=485, altitude=75)

    print("Wing load: ", state.get_wing_load())
    print("Safe load: ", state.get_safe_load())
    print("KTAS:", state.ktas, speed_fp_from_ktas(state.ktas))
    print("KEAS:", state.get_keas())
    print("Q:", state.get_q())
    print("Smash:", state.get_smash())
    print("Mach:", state.get_mach())
    print("Engine output:", state.get_engine_output())
    print("LCS:", state.get_lcs())
    print("Max load:", state.get_max_load())
    print("Corner speed:", state.calculate_corner_speed())

    history = PerformanceHistory(state)
    performance = history.resolve_turn(segment_pulls=22, delta_altitude=-15)
    print(performance.format())


if __name__ == "__main__":
    main()

import dataclasses
from dataclasses import dataclass, asdict, fields
import json
import math
import pathlib

from chart import IsobarChart, load_isobars
import performance


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
    roll_rate: dict[float, str]
    stores: Stores

    ab_engine_output: IsobarChart | None
    dry_engine_output: IsobarChart

    @classmethod
    def from_json(cls, path: pathlib.Path) -> "AircraftDataCard":
        with open(path, "r") as file:
            adc_dict = json.loads(file.read())

            # Let's replace the relative path to the engine chart with an actual
            # chart instance.
            dry_chart_file = path.parent / adc_dict["dry_engine_output"]
            adc_dict["dry_engine_output"] = IsobarChart(
                load_isobars(dry_chart_file))
            # AB engine output is optional.
            try:
                ab_chart_file = path.parent / adc_dict["ab_engine_output"]
                adc_dict["ab_engine_output"] = IsobarChart(
                    load_isobars(ab_chart_file))
            except KeyError:
                # Expected, this plane does not have an afterburning engine.
                adc_dict["ab_engine_output"] = None

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
        return round(performance.keas_from_ktas(self.ktas, self.altitude))

    def get_q(self) -> float:
        keas = self.get_keas()
        # q = keas² / 2950.3 (or keas² * 0.000339); empirically determined,
        # refit against e6b/q.csv after correcting a transcription typo
        # (103.5->7 was actually 143.5->7) that had skewed the original fit.
        return round(keas**2 / 2950.3, 1)

    def get_smash(self) -> float:
        return round(10.0 * self.get_q() / self.get_wing_load(), 1)

    def get_mach(self) -> float:
        mach = performance.mach_from_keas(self.get_keas(), self.altitude)
        # Two decimals, not one: the ADC lift/drag tables have rows only
        # 0.03-0.06 mach apart, so one-decimal rounding (0.78 -> 0.8) skips
        # past the row the slide-rule reading actually selects.
        return round(mach, 2)

    def get_engine_output(self, afterburner: bool = True) -> float:
        if afterburner and self.adc.ab_engine_output:
            chart = self.adc.ab_engine_output
        else:
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

    def get_lift_limited_load(self) -> float:
        """The load the wing makes at alpha_max, unrounded."""
        return self.adc.lift.alpha_max / self.get_lcs() * self.get_smash()

    def get_max_load(self) -> int:
        # We don't want to exceed our max load ever, hence we round down.
        return math.floor(self.get_lift_limited_load())

    def get_roll_rate(self) -> str:
        smash = self.get_smash()
        return _bop_tablerow_lookup(smash, self.adc.roll_rate)

    def get_total_drag(self) -> float:
        form_drag = self.get_form_drag()
        # TODO(mbroecker): Enable through toggle.
        brake_drag = 0.0
        stores_drag = 0.0
        return form_drag + brake_drag + stores_drag

    def get_form_delta_ktas(self) -> float:
        return self.get_total_drag() * self.get_smash() / 10

    def get_engine_scale(self) -> float:
        return performance.engine_scale(self.altitude, self.get_mach())

    def get_engine_delta_ktas(self, engine_output: float) -> float:
        # The p-alt/mach engine scale turns engine output into base delta
        # knots at combat weight; a heavier aircraft gains proportionally less.
        base_delta_ktas = engine_output / self.get_engine_scale()
        return base_delta_ktas * self.adc.stores.combat_weight / self.weight


def speed_fp_from_ktas(ktas: float) -> int:
    if ktas < 60:
        return 1
    return 2 + int((ktas - 60) // 40)


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
    engine_output: float
    engine_delta_ktas: float
    afterburner: bool

    old_state: AircraftState
    new_state: AircraftState

    @property
    def gs(self) -> float:
        return gs_from_pulls(self.segment_pulls)

    @property
    def new_speed_fp(self) -> int:
        return speed_fp_from_ktas(self.new_state.ktas)

    @property
    def departs_flight(self) -> bool:
        # The rules don't define a speed of 0 or less: the aircraft departs
        # flight, so a turn that ends there is invalid.
        return self.new_state.ktas <= 0

    @property
    def max_alpha(self) -> float:
        return self.old_state.adc.lift.alpha_max

    @property
    def max_engine_output(self) -> float:
        # A property can't take an afterburner argument from the caller (it's
        # always invoked as a plain attribute access) -- self.afterburner is
        # what this turn actually used, so that's what "max available" has
        # to mean here, not always the afterburner-on max regardless of mode.
        return self.old_state.get_engine_output(self.afterburner)

    @property
    def smash(self) -> float:
        return self.old_state.get_smash()

    @property
    def engine_scale(self) -> float:
        return self.old_state.get_engine_scale()

    @property
    def base_engine_delta_ktas(self) -> float:
        return self.engine_output / self.engine_scale

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
            f"Engine  dKTAS: {round(self.engine_delta_ktas, 1)} (output {self.engine_output}, max {self.max_engine_output}, base {round(self.base_engine_delta_ktas, 1)})",
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
    afterburner: bool = True,
) -> TurnPerformance:
    # Structural load and sea level are hard limits, not suggestions -- clamp
    # here so every caller gets them for free, not just ones that also apply
    # the UI's own stepper limits.
    segment_pulls = min(segment_pulls, state.get_max_load())
    delta_altitude = max(delta_altitude, -state.altitude)

    # FP always comes from KTAS; KEAS only feeds the intermediate
    # performance values (q, smash, mach and the table lookups).
    speed = speed_fp_from_ktas(state.ktas)
    if segment_fp:
        # A segment can't be longer than the FP your current speed actually
        # allows, and a segment of length <= 0 makes the load division below
        # meaningless -- clamp to [1, speed] rather than trusting the input.
        segment_fp = max(1, min(segment_fp, speed))
        load = float(segment_pulls) / segment_fp * speed
    else:
        segment_fp = speed
        load = float(segment_pulls)

    # No pull makes no alpha -- including at a speed too slow for any lift
    # (smash 0), where max load is 0 and pulls are already clamped to it.
    alpha = load / state.get_smash() * state.get_lcs() if load else 0.0

    dl = alpha / state.get_ids() * load * 100
    induced_delta_ktas = dl / speed * segment_fp

    gravity_delta_ktas = float(delta_altitude) / speed * 60 * -1
    form_delta_ktas = state.get_form_delta_ktas()

    # A caller may set this explicitly (e.g. a throttle setting other than
    # max); either way it's capped by whichever chart the afterburner toggle
    # selects -- you can't request more thrust than that mode actually has.
    max_engine_output = state.get_engine_output(afterburner)
    used_engine_output = (
        max_engine_output
        if engine_output is None
        else min(engine_output, max_engine_output)
    )
    engine_delta_ktas = state.get_engine_delta_ktas(used_engine_output)

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
        engine_output=used_engine_output,
        engine_delta_ktas=engine_delta_ktas,
        afterburner=afterburner,
        old_state=state,
        new_state=new_state,
    )


def max_flying_pulls(
    state: AircraftState,
    segment_fp: int | None = None,
    delta_altitude: int = 0,
    engine_output: float | None = None,
    afterburner: bool = True,
) -> int | None:
    """The most whole pulls, up to max load, whose turn with these inputs
    doesn't depart flight -- or None if even no pull at all keeps the
    aircraft flying (e.g. a climb steeper than its speed can pay for)."""
    for pulls in range(state.get_max_load(), -1, -1):
        turn = calculate_performance(
            state,
            pulls,
            segment_fp=segment_fp,
            delta_altitude=delta_altitude,
            engine_output=engine_output,
            afterburner=afterburner,
        )
        if not turn.departs_flight:
            return pulls
    return None


class DepartedFlightError(ValueError):
    """Raised by PerformanceHistory.resolve_turn() for a turn that would end
    at 0 KTAS or less: the aircraft departs flight, so the turn is refused
    rather than recorded."""

    def __init__(self, turn: TurnPerformance, max_flying_pulls: int | None):
        self.turn = turn
        self.max_flying_pulls = max_flying_pulls
        if max_flying_pulls is None:
            advice = "no pull keeps it flying"
        else:
            advice = f"at most {max_flying_pulls} keep it flying"
        super().__init__(
            f"Departs flight: {turn.segment_pulls:g} pulls would end at "
            f"{turn.new_state.ktas:.0f} KTAS -- {advice}."
        )


@dataclass(frozen=True)
class SustainedTurnPoint:
    """One point on a sustained-turn-performance sweep: what an aircraft can
    do at one particular speed and altitude, energy-wise and structurally.
    """

    ktas: float
    mach: float
    speed_fp: int
    # Unrounded by default; with fractional=False, whole loads rounded down,
    # since pulls are whole numbers in play.
    sustained_load: float
    max_load: float
    sustained_phad_cells: float
    max_phad_cells: float
    # The most a turn can pull: max_load, or less where pulling that much
    # would end the turn at 0 KTAS or less and depart flight.
    max_pullable_load: float
    # PHAD cells from max_pullable_load: the fastest turn possible at this
    # speed, even if it bleeds energy. Rounded down to whole cells with
    # fractional=False, like sustained_turn_cells.
    max_pullable_cells: float
    # PHAD cells this speed can turn indefinitely: the cells from the lower
    # of sustained_load and max_load, since a load above either one either
    # bleeds speed or can't be pulled at all.
    sustained_turn_cells: float
    engine_output: float
    total_drag: float


@dataclass(frozen=True)
class BestSustainedTurn:
    """Where a performance.sustained_turn_profile() sweep's sustained_load and max_load
    curves cross -- the fastest speed still limited by the airframe rather
    than energy. Below this speed, more Gs are available than the aircraft
    can structurally use; above it, more Gs are structurally available than
    the aircraft has the energy to sustain. Pulling max load every turn
    settles here whatever the starting speed.

    This is NOT necessarily the best sustained turn rate: the PHAD-cell
    rate also falls with airspeed, so a faster speed holding more load can
    out-turn it. See BestSustainedTurnRate for that.
    """

    ktas: float
    load: float
    phad_cells: float


@dataclass(frozen=True)
class BestSustainedTurnRate:
    """The single best sustained turn in a performance.sustained_turn_profile()
    sweep: the most PHAD cells any speed can hold indefinitely, at the
    slowest speed that holds them (the same cells at a lower speed turn a
    tighter circle), and the load to pull there.
    """

    cells: float
    ktas: float
    load: float


@dataclass(frozen=True)
class StructuralCornerPoint:
    """The classic aerodynamic "corner speed": the interpolated point where
    the lift-limited max_load curve first reaches the aircraft's structural
    G rating (combat_safe_load). Purely aerodynamic/structural -- unlike
    BestSustainedTurn, it doesn't touch thrust or drag at all, so it says
    nothing about whether that speed/G is actually sustainable. In fact it
    usually isn't: reaching it is typically a one-time, energy-spending
    maneuver rather than something performance.sustained_load() could hold.
    """

    ktas: float
    load: float


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
        afterburner: bool = True,
    ) -> TurnPerformance:
        inputs = dict(
            segment_fp=segment_fp,
            delta_altitude=delta_altitude,
            engine_output=engine_output,
            afterburner=afterburner,
        )
        turn = calculate_performance(self.current_state, segment_pulls, **inputs)
        if turn.departs_flight:
            raise DepartedFlightError(turn, max_flying_pulls(self.current_state, **inputs))
        self.turns.append(turn)
        return turn

    def format(self) -> str:
        return "\n".join(turn.format() for turn in self.turns)


def main() -> None:
    adc = AircraftDataCard.from_json(pathlib.Path("adc/swift-mk5.json"))
    state = AircraftState(adc, weight=17.4, ktas=385, altitude=35)

    print("Wing load: ", state.get_wing_load())
    print("Safe load: ", state.get_safe_load())
    print("KTAS:", state.ktas, speed_fp_from_ktas(state.ktas))
    print("KEAS:", state.get_keas())
    print("Q:", state.get_q())
    print("Smash:", state.get_smash())
    print("Mach:", state.get_mach())
    print("Roll rate:", state.get_roll_rate())
    print("Engine output:", state.get_engine_output())
    print("LCS:", state.get_lcs())
    print("Max load:", state.get_max_load())
    print("Corner speed:", performance.corner_speed(state))

    history = PerformanceHistory(state)
    turn = history.resolve_turn(segment_pulls=22, delta_altitude=-15)
    print(turn.format())


if __name__ == "__main__":
    main()

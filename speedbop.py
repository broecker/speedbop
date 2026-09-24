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

    def get_sustained_load(self, afterburner: bool = True) -> float:
        """Max load (same units as get_max_load()) sustainable indefinitely
        at this exact state -- the load at which induced + form drag exactly
        balances engine thrust, so KTAS neither rises nor falls turn over
        turn. Above this (up to get_max_load()'s structural ceiling), every
        turn bleeds energy; below it, every turn gains energy.

        Closed-form, not iterative: in calculate_performance(), with no
        segment_fp override, induced_delta_ktas reduces to load**2 * k for
        a k that doesn't depend on load, and neither form_delta_ktas nor
        engine_delta_ktas depend on load at all -- so solving for the load
        where the net change is zero is just inverting that square. Unlike
        get_max_load(), this is an analysis value, not a live per-turn
        clamp, so it's left as a precise float (not floored to an int) and
        NOT capped at get_max_load() -- callers wanting the actually
        achievable sustained G take min(get_sustained_load(), get_max_load())
        themselves.
        """
        net_thrust = self.get_engine_output(afterburner) - self.get_total_drag()
        if net_thrust <= 0:
            return 0.0
        smash = self.get_smash()
        if smash == 0:
            # No meaningful lift at this speed (q/smash -> 0 as ktas -> 0) --
            # there's no load, however small, it can sustain.
            return 0.0
        k = 100 * self.get_lcs() / (smash * self.get_ids())
        return math.sqrt(net_thrust / k)


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
    afterburner: bool

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
        # A property can't take an afterburner argument from the caller (it's
        # always invoked as a plain attribute access) -- self.afterburner is
        # what this turn actually used, so that's what "max available" has
        # to mean here, not always the afterburner-on max regardless of mode.
        return self.old_state.get_engine_output(self.afterburner)

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
    afterburner: bool = True,
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

    # A caller may set this explicitly (e.g. a throttle setting other than
    # max); either way it's capped by whichever chart the afterburner toggle
    # selects -- you can't request more thrust than that mode actually has.
    max_engine_output = state.get_engine_output(afterburner)
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
        afterburner=afterburner,
        old_state=state,
        new_state=new_state,
    )


@dataclass(frozen=True)
class SustainedTurnPoint:
    """One point on a sustained-turn-performance sweep: what an aircraft can
    do at one particular speed and altitude, energy-wise and structurally.
    """

    ktas: float
    mach: float
    sustained_load: float
    max_load: int
    engine_output: float
    total_drag: float


def sustained_turn_profile(
    adc: AircraftDataCard,
    weight: float,
    altitude: int,
    ktas_values: list[float],
    afterburner: bool = True,
) -> list[SustainedTurnPoint]:
    """Sustained-turn performance across a range of speeds at one altitude --
    the data behind an energy-maneuverability chart: for each speed, how
    many Gs can this aircraft sustain indefinitely (sustained_load) versus
    how many Gs the airframe can take at all (max_load). See
    find_best_sustained_turn() for where those two curves cross.

    The speed range is entirely up to the caller (a chart, a script, a
    test) -- this just builds one AircraftState per KTAS value and reads
    its performance off it, the same way any other turn-by-turn state would.
    """
    points = []
    for ktas in ktas_values:
        state = AircraftState(adc, weight, ktas, altitude)
        points.append(
            SustainedTurnPoint(
                ktas=ktas,
                mach=state.get_mach(),
                sustained_load=state.get_sustained_load(afterburner),
                max_load=state.get_max_load(),
                engine_output=state.get_engine_output(afterburner),
                total_drag=state.get_total_drag(),
            )
        )
    return points


@dataclass(frozen=True)
class BestSustainedTurn:
    """Where a sustained_turn_profile() sweep's sustained_load and max_load
    curves cross -- the fastest speed still limited by the airframe rather
    than energy. Below this speed, more Gs are available than the aircraft
    can structurally use; above it, more Gs are structurally available than
    the aircraft has the energy to sustain -- so this crossing is the best
    sustained turn an aircraft can fly (most Gs it can BOTH survive AND
    hold indefinitely at once).
    """

    ktas: float
    load: float


def find_best_sustained_turn(points: list[SustainedTurnPoint]) -> BestSustainedTurn | None:
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
    """
    flying = [p for p in points if p.max_load > 0]

    for prev, curr in zip(flying, flying[1:]):
        prev_diff = prev.sustained_load - prev.max_load
        curr_diff = curr.sustained_load - curr.max_load

        if prev_diff == 0:
            return BestSustainedTurn(ktas=prev.ktas, load=prev.sustained_load)
        if (prev_diff < 0) != (curr_diff < 0):
            t = prev_diff / (prev_diff - curr_diff)
            return BestSustainedTurn(
                ktas=prev.ktas + t * (curr.ktas - prev.ktas),
                load=prev.sustained_load + t * (curr.sustained_load - prev.sustained_load),
            )
    return None


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
        performance = calculate_performance(
            self.current_state,
            segment_pulls,
            segment_fp=segment_fp,
            delta_altitude=delta_altitude,
            engine_output=engine_output,
            afterburner=afterburner,
        )
        self.turns.append(performance)
        return performance

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
    print("Engine output:", state.get_engine_output())
    print("LCS:", state.get_lcs())
    print("Max load:", state.get_max_load())
    print("Corner speed:", state.calculate_corner_speed())

    history = PerformanceHistory(state)
    performance = history.resolve_turn(segment_pulls=22, delta_altitude=-15)
    print(performance.format())


if __name__ == "__main__":
    main()

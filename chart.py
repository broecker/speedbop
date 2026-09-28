"""Interpolate 2D performance charts represented as labeled isobars.

Some calculations aren't formulas at all -- they're read directly off a 2D
chart (e.g. engine output vs. altitude and Mach), where families of labeled
contour lines ("isobars") show curves of constant output. There's no
closed-form relationship to fit here (see e6b/fit.py for that); the chart
IS the data, and querying it means interpolating between the two nearest
isobars the same way you'd read the chart by hand:

1. Slice every isobar at the query altitude: interpolate along each
   isobar's own digitized points to find the Mach value it crosses at that
   altitude.
2. Interpolate across isobars by Mach: sort the sliced (mach, output)
   points and interpolate the output value between the two that bracket
   the query Mach.

Both steps are the same primitive (1D linear interpolation) with the axes
swapped. This module rolls its own interpolation (no numpy/scipy) since a
handful of digitized points and two linear-interpolation passes don't need
either dependency -- deliberately: unlike e6b/fit.py (a numpy/scipy-heavy
dev-time tool for deriving formulas offline), this module is imported by
speedbop.py itself and runs at actual gameplay time, including inside
Pyodide in the browser. It lives at the repo root rather than inside e6b/
for exactly that reason: e6b/ is the offline formula-derivation toolkit,
never imported by speedbop.py, while this is a runtime dependency.

Isobars must not cross one another -- IsobarChart checks this once at
construction, across every digitized altitude (not just a single query
point: two isobars can look fine at any one altitude and still cross
somewhere between two others -- catching that needs looking at the whole
digitized range, not the query). A crossing almost always means a
transcription error, not a real feature of the chart. Queries outside the
digitized range are clamped to the nearest edge rather than extrapolated,
at both the per-isobar and cross-isobar steps.

Usage:
    isobars = load_isobars("engine_chart.csv")
    chart = IsobarChart(isobars)
    chart.interpolate(altitude=150, mach=1.2)
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import pathlib


def _interp(x: float, xs: list[float], ys: list[float]) -> float:
    """Linear interpolation of y at x, given xs sorted ascending.

    Clamps to the nearest endpoint instead of extrapolating when x falls
    outside [xs[0], xs[-1]].
    """
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            x0, x1 = xs[i - 1], xs[i]
            if x1 == x0:
                return ys[i - 1]
            t = (x - x0) / (x1 - x0)
            return ys[i - 1] + t * (ys[i] - ys[i - 1])
    raise AssertionError("unreachable: x is within [xs[0], xs[-1]] by the checks above")


@dataclass
class Isobar:
    """One labeled contour line: points that all share the same output value.

    altitude/mach are the digitized points along the curve; they're sorted
    by altitude on construction, so points don't need to be given in order.
    """

    output: float
    altitude: list[float]
    mach: list[float]

    def __post_init__(self) -> None:
        if len(self.altitude) != len(self.mach):
            raise ValueError("altitude and mach must have the same number of points")
        if len(self.altitude) < 2:
            raise ValueError("an isobar needs at least 2 points to interpolate along")
        pairs = sorted(zip(self.altitude, self.mach))
        self.altitude = [a for a, _ in pairs]
        self.mach = [m for _, m in pairs]

    def mach_at(self, altitude: float) -> float:
        """Mach value this isobar crosses at the given altitude.

        Clamped to this isobar's own digitized range -- an isobar doesn't
        necessarily span the chart's full altitude axis.
        """
        return _interp(altitude, self.altitude, self.mach)


@dataclass
class IsobarChart:
    """A performance chart: a family of isobars over (altitude, mach)."""

    isobars: list[Isobar]

    def __post_init__(self) -> None:
        if len(self.isobars) < 2:
            raise ValueError("need at least 2 isobars to interpolate between")
        self.isobars = sorted(self.isobars, key=lambda iso: iso.output)
        self._check_no_crossings()

    def _check_no_crossings(self) -> None:
        # Requires ONE consistent direction shared by every adjacent pair
        # (by output) -- e.g. higher output = higher mach, everywhere, for
        # the whole family -- which is what a normal engine performance
        # chart looks like. Checking only at the endpoints, or only at a
        # single query altitude, can miss a crossing that happens strictly
        # between two breakpoints of a DIFFERENT isobar, so this samples
        # the union of every isobar's own altitude points.
        breakpoints = sorted({a for iso in self.isobars for a in iso.altitude})
        direction = 0
        for altitude in breakpoints:
            # Paired with their isobar so a crossing can be reported by the
            # isobar's own output label (what's actually printed on the
            # chart and in the CSV) instead of just the raw mach values at
            # the crossing point -- output is what you'd actually go look
            # up to find and fix a transcription error.
            labeled = list(zip(self.isobars, (iso.mach_at(altitude) for iso in self.isobars)))
            for (lower_iso, lower_mach), (upper_iso, upper_mach) in zip(labeled, labeled[1:]):
                diff = upper_mach - lower_mach
                if diff == 0:
                    continue
                sign = 1 if diff > 0 else -1
                if direction == 0:
                    direction = sign
                elif sign != direction:
                    raise ValueError(
                        f"isobar {lower_iso.output} and isobar {upper_iso.output} cross "
                        f"near altitude {altitude} (mach {lower_mach} vs {upper_mach}): "
                        "their relative mach order isn't consistent across the chart. "
                        "Check the digitized points for a transcription error -- "
                        "isobars must not cross."
                    )

    def interpolate(self, altitude: float, mach: float) -> float:
        """Output value at (altitude, mach), clamped at the chart's edges."""
        sliced = sorted((iso.mach_at(altitude), iso.output) for iso in self.isobars)
        machs = [m for m, _ in sliced]
        outputs = [o for _, o in sliced]
        return _interp(mach, machs, outputs)

    def to_rows(self) -> list[dict]:
        """Every digitized point as a flat (output, altitude, mach) dict.

        The same shape load_isobars() reads back in -- useful for exporting
        or plotting the chart as-loaded, e.g. rendering it in a UI.
        """
        return [
            {"output": iso.output, "altitude": a, "mach": m}
            for iso in self.isobars
            for a, m in zip(iso.altitude, iso.mach)
        ]


def load_isobars(path: str) -> list[Isobar]:
    """Load isobars from a CSV with columns output,altitude,mach.

    Rows sharing the same output value are one isobar's digitized points.
    """
    groups: dict[float, list[tuple[float, float]]] = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            output = float(row["output"])
            groups.setdefault(output, []).append(
                (float(row["altitude"]), float(row["mach"]))
            )

    return [
        Isobar(
            output=output, altitude=[a for a, _ in points], mach=[m for _, m in points]
        )
        for output, points in groups.items()
    ]


def validate_engine_charts() -> bool:
    """Validate every engine output chart referenced from adc/index.json.

    Prints one PASS/FAIL line per chart file rather than stopping at the
    first bad one, so a single run reports every problem across the whole
    roster instead of needing to be re-run after each fix. A FAIL line
    includes whatever IsobarChart's own construction-time check raised --
    for a crossing, that names the two isobars involved by their output
    label (see IsobarChart._check_no_crossings) so the offending points
    are easy to find in the CSV.

    Returns True if every chart is valid; the CLI entry point below turns
    that into a process exit code, so this can be used as a pass/fail
    check (e.g. before committing a new or edited engine chart).
    """
    # (aircraft name, engine mode, chart path) -- both name and mode are
    # needed to tell two charts for the same aircraft apart (dry vs AB).
    engine_charts: list[tuple[str, str, pathlib.Path]] = []
    base_path = pathlib.Path("adc")
    with open(base_path / "index.json", "r") as index_file:
        entries = json.load(index_file)
        for entry in entries:
            adc_path = base_path / entry["path"]
            with open(adc_path, "r") as adc_file:
                adc = json.load(adc_file)
            for mode, key in [("dry", "dry_engine_output"), ("AB", "ab_engine_output")]:
                if key in adc:
                    engine_charts.append((entry["name"], mode, adc_path.parent / adc[key]))

    all_valid = True
    for name, mode, chart_path in engine_charts:
        label = f"{name} ({mode}, {chart_path})"
        try:
            IsobarChart(load_isobars(chart_path))
        except Exception as e:
            all_valid = False
            print(f"FAIL  {label}\n      {e}")
        else:
            print(f"OK    {label}")

    return all_valid


if __name__ == "__main__":
    import sys

    sys.exit(0 if validate_engine_charts() else 1)

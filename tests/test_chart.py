import pytest

from e6b.chart import Isobar, IsobarChart, load_isobars, _interp


# ---------------------------------------------------------------------------
# _interp
# ---------------------------------------------------------------------------

def test_interp_linear_between_points():
    assert _interp(5.0, [0.0, 10.0], [0.0, 100.0]) == pytest.approx(50.0)


def test_interp_clamps_below_and_above_range():
    xs, ys = [10.0, 20.0], [1.0, 2.0]
    assert _interp(5.0, xs, ys) == pytest.approx(1.0)
    assert _interp(25.0, xs, ys) == pytest.approx(2.0)


def test_interp_handles_duplicate_x_without_dividing_by_zero():
    assert _interp(10.0, [0.0, 10.0, 10.0, 20.0], [0.0, 5.0, 7.0, 10.0]) == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Isobar
# ---------------------------------------------------------------------------

def test_isobar_sorts_points_by_altitude_regardless_of_input_order():
    iso = Isobar(output=100.0, altitude=[150.0, 0.0, 50.0], mach=[1.4, 0.9, 1.1])

    assert iso.altitude == [0.0, 50.0, 150.0]
    assert iso.mach == [0.9, 1.1, 1.4]


def test_isobar_mach_at_interpolates_and_clamps():
    iso = Isobar(output=100.0, altitude=[0.0, 100.0], mach=[0.9, 1.4])

    assert iso.mach_at(50.0) == pytest.approx(1.15)
    assert iso.mach_at(-50.0) == pytest.approx(0.9)   # clamped, not extrapolated
    assert iso.mach_at(500.0) == pytest.approx(1.4)   # clamped, not extrapolated


def test_isobar_requires_at_least_two_points():
    with pytest.raises(ValueError, match="at least 2 points"):
        Isobar(output=100.0, altitude=[0.0], mach=[0.9])


def test_isobar_requires_matching_lengths():
    with pytest.raises(ValueError, match="same number of points"):
        Isobar(output=100.0, altitude=[0.0, 50.0], mach=[0.9])


# ---------------------------------------------------------------------------
# IsobarChart
# ---------------------------------------------------------------------------

def _simple_chart() -> IsobarChart:
    # Two isobars, both spanning altitude 0-300: at any given altitude,
    # the 100-output isobar sits at a higher mach than the 50-output one.
    low = Isobar(output=50.0, altitude=[0.0, 150.0, 300.0], mach=[0.5, 0.8, 1.0])
    high = Isobar(output=100.0, altitude=[0.0, 150.0, 300.0], mach=[0.9, 1.4, 2.0])
    return IsobarChart([high, low])  # deliberately out of order -- constructor must sort


def test_chart_sorts_isobars_by_output():
    chart = _simple_chart()

    assert [iso.output for iso in chart.isobars] == [50.0, 100.0]


def test_chart_matches_isobar_value_exactly_on_the_line():
    chart = _simple_chart()

    # at altitude=0, the 50-isobar sits exactly at mach=0.5
    assert chart.interpolate(altitude=0.0, mach=0.5) == pytest.approx(50.0)
    assert chart.interpolate(altitude=0.0, mach=0.9) == pytest.approx(100.0)


def test_chart_interpolates_between_two_isobars():
    chart = _simple_chart()
    # at altitude=0: 50-isobar at mach=0.5, 100-isobar at mach=0.9.
    # query mach=0.7 is exactly halfway -> output should be halfway too.
    assert chart.interpolate(altitude=0.0, mach=0.7) == pytest.approx(75.0)


def test_chart_clamps_mach_outside_the_bracketing_isobars():
    chart = _simple_chart()

    assert chart.interpolate(altitude=0.0, mach=0.1) == pytest.approx(50.0)   # below lowest
    assert chart.interpolate(altitude=0.0, mach=5.0) == pytest.approx(100.0)  # above highest


def test_chart_clamps_altitude_outside_an_isobars_own_range():
    chart = _simple_chart()

    # altitude=-50 clamps each isobar to its altitude=0 point before
    # slicing, so this should equal the altitude=0 result exactly.
    assert chart.interpolate(altitude=-50.0, mach=0.7) == pytest.approx(
        chart.interpolate(altitude=0.0, mach=0.7)
    )


def test_chart_requires_at_least_two_isobars():
    with pytest.raises(ValueError, match="at least 2 isobars"):
        IsobarChart([Isobar(output=50.0, altitude=[0.0, 100.0], mach=[0.5, 0.8])])


def test_chart_detects_crossing_isobars():
    # These cross partway up: the 50-isobar starts below the 100-isobar
    # but ends above it, which shouldn't be physically possible. Caught at
    # construction time -- a single query altitude wouldn't necessarily
    # see the crossing (at altitude=300 alone, [2.0, 1.0] looks perfectly
    # monotonic; the crossing is only visible looking across the range).
    low = Isobar(output=50.0, altitude=[0.0, 300.0], mach=[0.5, 2.0])
    high = Isobar(output=100.0, altitude=[0.0, 300.0], mach=[0.9, 1.0])

    with pytest.raises(ValueError, match="isobars cross"):
        IsobarChart([low, high])


def test_chart_works_with_descending_mach_convention():
    # Some charts might run the other way: higher output = lower mach at a
    # given altitude. The bracket-and-interpolate logic must not assume
    # which direction is "normal".
    low_output_high_mach = Isobar(output=50.0, altitude=[0.0, 300.0], mach=[2.0, 2.0])
    high_output_low_mach = Isobar(output=100.0, altitude=[0.0, 300.0], mach=[1.0, 1.0])

    chart = IsobarChart([low_output_high_mach, high_output_low_mach])

    assert chart.interpolate(altitude=0.0, mach=1.5) == pytest.approx(75.0)


# ---------------------------------------------------------------------------
# load_isobars
# ---------------------------------------------------------------------------

def test_load_isobars_groups_rows_by_output(tmp_path):
    path = tmp_path / "chart.csv"
    path.write_text(
        "output,altitude,mach\n"
        "50,0,0.5\n"
        "50,300,1.0\n"
        "100,0,0.9\n"
        "100,300,2.0\n"
    )

    isobars = load_isobars(str(path))

    assert {iso.output for iso in isobars} == {50.0, 100.0}
    chart = IsobarChart(isobars)
    assert chart.interpolate(altitude=0.0, mach=0.7) == pytest.approx(75.0)


# ---------------------------------------------------------------------------
# Real chart: e6b/engine.csv
# ---------------------------------------------------------------------------

def test_real_engine_chart_loads_and_interpolates():
    # Real digitized isobars from an aircraft data card's engine output vs.
    # altitude/Mach chart. The first version of this data had isobars 65
    # and 70 crossing around altitude=245 (output=70's altitude=310 point
    # was originally transcribed as mach=0.6; the corrected chart has it
    # at mach=0.26) -- confirmed fixed by the fact that this loads at all,
    # since IsobarChart raises on any crossing.
    chart = IsobarChart(load_isobars("e6b/engine.csv"))

    assert len(chart.isobars) == 10

    # points taken directly from the digitized data should round-trip exactly
    assert chart.interpolate(altitude=175.0, mach=1.45) == pytest.approx(40.0)
    assert chart.interpolate(altitude=0.0, mach=0.94) == pytest.approx(30.0)

    # isobars 65/70 no longer cross anywhere in the digitized range
    iso65 = next(iso for iso in chart.isobars if iso.output == 65.0)
    iso70 = next(iso for iso in chart.isobars if iso.output == 70.0)
    for altitude in range(0, 311, 10):
        assert iso65.mach_at(altitude) >= iso70.mach_at(altitude)

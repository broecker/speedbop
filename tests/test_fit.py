import numpy as np
import pytest

from e6b.fit import fit_power_law, validate


def test_recovers_exact_time_speed_distance_ratio():
    # time = distance / speed is a pure power law (k=1, p_distance=1, p_speed=-1),
    # exactly the kind of relationship the E6B's calculator side computes.
    rng = np.random.default_rng(0)
    distances = rng.uniform(10, 500, size=12)
    speeds = rng.uniform(20, 300, size=12)
    samples = [
        {"distance": d, "speed": s, "time": d / s}
        for d, s in zip(distances, speeds)
    ]

    result = fit_power_law(samples, output_key="time")

    assert result.r_squared == pytest.approx(1.0, abs=1e-9)
    assert result.k == pytest.approx(1.0, abs=1e-6)
    assert result.exponents["distance"] == pytest.approx(1.0, abs=1e-6)
    assert result.exponents["speed"] == pytest.approx(-1.0, abs=1e-6)


def test_validate_reports_zero_error_for_exact_fit():
    samples = [{"a": a, "b": b, "y": 3.0 * a**2 * b**-1} for a, b in [(1, 2), (2, 3), (3, 1), (4, 5)]]
    fit_samples, holdout_samples = samples[:3], samples[3:]

    result = fit_power_law(fit_samples, output_key="y")
    rows = validate(result, holdout_samples, output_key="y")

    assert len(rows) == 1
    assert rows[0]["rel_error"] == pytest.approx(0.0, abs=1e-6)


def test_low_r_squared_flags_non_ratio_relationships():
    # additive relationship (not a power law) -- e.g. a temperature-style
    # correction -- should NOT fit cleanly, so R^2 should visibly drop.
    samples = [{"x": x, "y": 10.0 + 2.0 * x} for x in [1, 2, 3, 5, 8, 13, 21]]

    result = fit_power_law(samples, output_key="y")

    assert result.r_squared < 0.999


@pytest.mark.parametrize("bad_value", [0, -5])
def test_rejects_non_positive_values_instead_of_hanging(bad_value):
    # log(0)/log(negative) produces -inf/NaN, which can make the SVD solver
    # inside np.linalg.lstsq spin for a very long time instead of raising.
    # This must fail fast with a clear error before reaching the solver.
    samples = [
        {"distance": 60, "speed": 120, "time": 0.5},
        {"distance": bad_value, "speed": 120, "time": 1.0},
        {"distance": 90, "speed": 90, "time": 1.0},
    ]

    with pytest.raises(ValueError, match="strictly positive"):
        fit_power_law(samples, output_key="time")

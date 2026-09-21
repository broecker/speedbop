import numpy as np
import pytest

from e6b.fit import fit_power_law, fit_shifted_power_window, load_samples, validate


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
    assert result.ratio_exponents["distance"] == pytest.approx(1.0, abs=1e-6)
    assert result.ratio_exponents["speed"] == pytest.approx(-1.0, abs=1e-6)


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


def test_fits_window_set_variable_including_zero():
    # A window-set input (e.g. altitude dialed into a ring-offset window)
    # multiplies by exp(c * value) rather than value ** p, and must accept
    # 0 -- dialing altitude to 0 just means "no correction applied", which
    # would previously have failed validation as a power-law input.
    rng = np.random.default_rng(1)
    cas = rng.uniform(80, 250, size=10)
    altitude_thousands = rng.uniform(0, 25, size=10)
    c = 0.018
    samples = [
        {"cas": v, "altitude_thousands": a, "tas": v * np.exp(c * a)}
        for v, a in zip(cas, altitude_thousands)
    ]
    samples[0] = {"cas": 120.0, "altitude_thousands": 0.0, "tas": 120.0}

    result = fit_power_law(samples, output_key="tas", linear_keys={"altitude_thousands"})

    assert result.r_squared == pytest.approx(1.0, abs=1e-9)
    assert result.k == pytest.approx(1.0, abs=1e-6)
    assert result.ratio_exponents["cas"] == pytest.approx(1.0, abs=1e-6)
    assert result.linear_coefficients["altitude_thousands"] == pytest.approx(c, abs=1e-6)
    assert result.predict(cas=100, altitude_thousands=0) == pytest.approx(100.0, abs=1e-6)


def test_fits_nonlinear_window_compressed_near_zero_stretched_away():
    # A window dial that's compressed near one point and stretches away from
    # it in both directions (and can go negative) isn't a uniform-rotation
    # window (exp(c*x)) -- model it as exp(c * sign(x-offset) * |x-offset|^n)
    # instead, fit via nonlinear least squares. True offset=0, n=2.5, spanning
    # negative values (down to -10, matching a real E6BoP altitude window).
    rng = np.random.default_rng(2)
    cas = rng.uniform(80, 250, size=16)
    altitude = rng.uniform(-10, 25, size=16)
    c_true, n_true, offset_true = 0.0015, 2.5, 0.0
    tas = cas * np.exp(
        c_true * np.sign(altitude - offset_true) * np.abs(altitude - offset_true) ** n_true
    )
    samples = [{"cas": v, "altitude": a, "tas": t} for v, a, t in zip(cas, altitude, tas)]
    fit_samples, holdout_samples = samples[:-3], samples[-3:]

    result = fit_shifted_power_window(fit_samples, output_key="tas", window_key="altitude")

    assert result.r_squared == pytest.approx(1.0, abs=1e-6)
    assert result.ratio_exponents["cas"] == pytest.approx(1.0, abs=1e-3)
    assert result.c == pytest.approx(c_true, abs=1e-4)
    assert result.n == pytest.approx(n_true, abs=1e-2)
    assert result.offset == pytest.approx(offset_true, abs=1e-2)

    rows = validate(result, holdout_samples, output_key="tas")
    assert all(row["rel_error"] < 1e-3 for row in rows)


def test_pinning_known_exponent_and_k_matches_manual_single_parameter_fit():
    # KEAS == KTAS at sea level by definition -- that's a known boundary
    # condition, not something to fit. Letting k and the keas exponent
    # float anyway lets the regression spend those degrees of freedom
    # absorbing noise from other altitudes, which can violate the known
    # identity at altitude=0 to buy a marginally better overall fit.
    rng = np.random.default_rng(4)
    keas = rng.uniform(50, 1200, size=12)
    altitude = rng.uniform(0, 200, size=12)
    c_true = 0.0034
    ktas = keas * np.exp(c_true * altitude)
    samples = [{"keas": v, "altitude": a, "ktas": t} for v, a, t in zip(keas, altitude, ktas)]

    result = fit_power_law(
        samples,
        output_key="ktas",
        linear_keys={"altitude"},
        fixed_ratio_exponents={"keas": 1.0},
        fixed_k=1.0,
    )

    assert result.k == 1.0
    assert result.ratio_exponents["keas"] == 1.0
    assert result.linear_coefficients["altitude"] == pytest.approx(c_true, abs=1e-6)
    assert result.r_squared == pytest.approx(1.0, abs=1e-9)
    # ktas must equal keas exactly at altitude=0 -- the whole point of pinning
    assert result.predict(keas=321.0, altitude=0.0) == pytest.approx(321.0, abs=1e-9)


def test_pinning_rejects_key_marked_both_linear_and_fixed():
    samples = [{"a": 1.0, "b": 2.0, "y": 4.0}, {"a": 2.0, "b": 3.0, "y": 12.0}]

    with pytest.raises(ValueError, match="can't be both linear and a fixed ratio exponent"):
        fit_power_law(samples, output_key="y", linear_keys={"a"}, fixed_ratio_exponents={"a": 1.0})


def test_q_is_dynamic_pressure_pinned_to_keas_squared():
    # q (dynamic pressure) is physically defined as q = 0.5*rho*V^2, so it
    # must scale as keas^2 exactly -- not just approximately fit one. Real
    # data from e6b/q.csv: pinning p=2 and fitting only k should match the
    # data about as well as letting p float (it does, R^2 barely changes),
    # while guaranteeing the physically-required exponent instead of
    # trusting the regression to land near 2 by coincidence.
    samples = load_samples("e6b/q.csv")

    general = fit_power_law(samples, output_key="q")
    pinned = fit_power_law(samples, output_key="q", fixed_ratio_exponents={"keas": 2.0})

    assert general.ratio_exponents["keas"] == pytest.approx(2.0, abs=0.05)
    assert pinned.ratio_exponents["keas"] == 2.0
    assert pinned.r_squared > 0.999
    # every reading except the smallest (keas=52, q=1 -- a rounded-to-the-
    # nearest-integer reading at very low magnitude) is within ~1.5%
    rows = validate(pinned, [s for s in samples if s["keas"] != 52], output_key="q")
    assert all(row["rel_error"] < 0.02 for row in rows)


def test_smash_is_an_exact_ratio_with_no_physical_meaning():
    # "smash" has no real-world correspondence (unlike ktas or q) -- it's
    # read off a window after two outer dials (wl, q) are set. There's no
    # physical law to pin an exponent against here, but the general
    # unconstrained fit still recovers an exact, clean formula: the device
    # is built from the same log-scaled rotating rings regardless of
    # whether the quantity it produces means anything outside the game.
    # One row in the raw readings (wl=40, q=100, smash=125) was a
    # confirmed outlier -- off by -78% vs every other row matching to
    # floating-point precision -- and was dropped rather than "corrected"
    # to a fabricated value, so e6b/smash.csv contains only real readings.
    samples = load_samples("e6b/smash.csv")

    result = fit_power_law(samples, output_key="smash")

    assert result.r_squared == pytest.approx(1.0, abs=1e-9)
    assert result.k == pytest.approx(10.0, abs=1e-6)
    assert result.ratio_exponents["wl"] == pytest.approx(-1.0, abs=1e-6)
    assert result.ratio_exponents["q"] == pytest.approx(1.0, abs=1e-6)
    assert result.predict(wl=40, q=100) == pytest.approx(25.0, abs=1e-6)

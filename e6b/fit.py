"""Derive formulas from E6B slide-rule sample readings.

The E6B takes inputs two different ways, and they need two different
formula shapes:

- Ring-read inputs are read directly off two rotating log-scaled rings
  against each other (the classic slide-rule multiply/divide), so they
  enter as power-law terms: output = k * input_1 ** p1 * input_2 ** p2 * ...

- Window-set inputs are dialed in through a window to set a rotational
  OFFSET between rings (e.g. an altitude or temperature correction window).
  Rotating a log-scaled ring by an amount proportional to a dialed value
  multiplies every subsequent reading by exp(c * value), not value ** p --
  so these enter as an exponential term instead, and may legitimately be
  zero or negative (dialing 0 just means "no correction applied"), unlike
  ring-read inputs which must be strictly positive.

Both forms are linear once you take the log of the output, so a handful of
readings taken directly off the device (vary one input at a time, across
its full range) is enough to recover k and every coefficient exactly via
least-squares regression -- and R^2 tells you whether a given step really
matches one of these shapes or whether it's something else (an additive
correction, a breakpoint table, trig) that needs a lookup table instead.

Usage:
    python -m e6b.fit samples.csv --output tas --holdout 2
    python -m e6b.fit samples.csv --output tas --linear altitude_thousands
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit


@dataclass
class FitResult:
    k: float
    ratio_exponents: dict[str, float]
    linear_coefficients: dict[str, float]
    r_squared: float
    n_samples: int

    def predict(self, **inputs: float) -> float:
        value = self.k
        for name, exp in self.ratio_exponents.items():
            value *= inputs[name] ** exp
        for name, coef in self.linear_coefficients.items():
            value *= np.exp(coef * inputs[name])
        return value

    def formula_str(self, output_name: str = "output") -> str:
        terms = [f"{name}^{exp:.4f}" for name, exp in self.ratio_exponents.items()]
        terms += [f"exp({coef:.4f}*{name})" for name, coef in self.linear_coefficients.items()]
        return f"{output_name} = {self.k:.6g}" + "".join(f" * {t}" for t in terms)


def fit_power_law(
    samples: list[dict[str, float]],
    output_key: str,
    linear_keys: frozenset[str] | set[str] = frozenset(),
    fixed_ratio_exponents: dict[str, float] | None = None,
    fixed_k: float | None = None,
) -> FitResult:
    """Fit output = k * prod(ratio_i ** p_i) * exp(sum(c_j * linear_j)).

    samples: list of dicts, each mapping variable name -> value.
    output_key: which field in each sample dict is the dependent variable.
    linear_keys: names of window-set inputs (see module docstring) to fit
        as an exponential term instead of a power term. These may be zero
        or negative. Every other input is treated as ring-read and must be
        strictly positive.
    fixed_ratio_exponents: known exponents for some ratio inputs, held
        constant instead of fit. Use this when a boundary condition in the
        data proves the true exponent exactly (e.g. two quantities defined
        to move 1:1) -- letting it float anyway just spends that degree of
        freedom absorbing noise from elsewhere in the data, which can pull
        the fit away from a value you already know is correct in exchange
        for a marginally better overall R^2.
    fixed_k: a known value for the leading constant k, held constant
        instead of fit (e.g. k=1 when the boundary condition above also
        pins the constant).
    """
    if len(samples) < 2:
        raise ValueError("need at least 2 samples to fit anything")

    input_keys = [k for k in samples[0] if k != output_key]
    if not input_keys:
        raise ValueError("samples must include at least one input variable")

    linear_keys = set(linear_keys)
    fixed_ratio_exponents = dict(fixed_ratio_exponents or {})
    unknown = linear_keys - set(input_keys)
    if unknown:
        raise ValueError(f"linear_keys not present in samples: {sorted(unknown)}")
    unknown = set(fixed_ratio_exponents) - set(input_keys)
    if unknown:
        raise ValueError(f"fixed_ratio_exponents not present in samples: {sorted(unknown)}")
    overlap = linear_keys & set(fixed_ratio_exponents)
    if overlap:
        raise ValueError(f"keys can't be both linear and a fixed ratio exponent: {sorted(overlap)}")

    ratio_keys = [k for k in input_keys if k not in linear_keys]
    linear_keys_ordered = [k for k in input_keys if k in linear_keys]
    free_ratio_keys = [k for k in ratio_keys if k not in fixed_ratio_exponents]

    for i, s in enumerate(samples):
        for k in (*ratio_keys, output_key):
            if s[k] <= 0:
                raise ValueError(
                    f"sample {i} has {k}={s[k]!r}, but log-log fitting requires "
                    "strictly positive values (0 or negative values produce "
                    "-inf/NaN, which can make the SVD solver in np.linalg.lstsq "
                    "hang instead of failing cleanly). If 0 is a real reading for "
                    "this variable, it's window-set rather than ring-read -- pass "
                    "it via linear_keys instead of fitting it as a power term."
                )

    y = np.log(np.array([s[output_key] for s in samples]))

    # subtract the known contribution of fixed terms, leaving only what's left to fit
    known = np.zeros(len(samples))
    for k, exp in fixed_ratio_exponents.items():
        known = known + exp * np.log([s[k] for s in samples])
    if fixed_k is not None:
        known = known + np.log(fixed_k)
    y_residual = y - known

    free_ratio_columns = [np.log([s[k] for s in samples]) for k in free_ratio_keys]
    linear_columns = [np.array([s[k] for s in samples], dtype=float) for k in linear_keys_ordered]
    columns = free_ratio_columns + linear_columns
    X = np.column_stack(columns) if columns else np.empty((len(samples), 0))

    if fixed_k is None:
        # augment with an intercept column to solve for log(k) alongside the coefficients
        A = np.column_stack([X, np.ones(len(samples))])
    else:
        if not columns:
            raise ValueError("nothing left to fit -- every exponent and k is fixed")
        A = X

    coeffs, *_ = np.linalg.lstsq(A, y_residual, rcond=None)
    n_free_ratio = len(free_ratio_keys)
    free_ratio_exponents = dict(zip(free_ratio_keys, coeffs[:n_free_ratio]))
    n_linear = len(linear_keys_ordered)
    linear_coefficients = dict(zip(linear_keys_ordered, coeffs[n_free_ratio : n_free_ratio + n_linear]))
    k_value = fixed_k if fixed_k is not None else float(np.exp(coeffs[-1]))

    ratio_exponents = {**fixed_ratio_exponents, **free_ratio_exponents}
    ratio_exponents = {rk: ratio_exponents[rk] for rk in ratio_keys}  # restore input order

    y_pred = known + A @ coeffs
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0

    return FitResult(
        k=k_value,
        ratio_exponents=ratio_exponents,
        linear_coefficients=linear_coefficients,
        r_squared=r_squared,
        n_samples=len(samples),
    )


@dataclass
class WindowFitResult:
    """Result of fit_shifted_power_window -- see that function's docstring."""

    log_k: float
    ratio_exponents: dict[str, float]
    window_key: str
    c: float
    n: float
    offset: float
    r_squared: float
    n_samples: int

    @property
    def k(self) -> float:
        return float(np.exp(self.log_k))

    def _window_term(self, x: float) -> float:
        shifted = x - self.offset
        return self.c * np.sign(shifted) * (abs(shifted) ** self.n)

    def predict(self, **inputs: float) -> float:
        value = self.k
        for name, exp in self.ratio_exponents.items():
            value *= inputs[name] ** exp
        value *= np.exp(self._window_term(inputs[self.window_key]))
        return value

    def formula_str(self, output_name: str = "output") -> str:
        terms = [f"{name}^{exp:.4f}" for name, exp in self.ratio_exponents.items()]
        terms.append(
            f"exp({self.c:.5g} * sign({self.window_key}-{self.offset:.4f}) "
            f"* |{self.window_key}-{self.offset:.4f}|^{self.n:.4f})"
        )
        return f"{output_name} = {self.k:.6g}" + "".join(f" * {t}" for t in terms)


def fit_shifted_power_window(
    samples: list[dict[str, float]],
    output_key: str,
    window_key: str,
    ratio_keys: list[str] | None = None,
    n_guess: float = 2.0,
) -> WindowFitResult:
    """Fit output = k * prod(ratio_i ** p_i) * exp(c * sign(x-offset) * |x-offset| ** n).

    Use this instead of fit_power_law's linear_keys when a window-set
    input's dial is visibly NONLINEAR -- e.g. compressed near one point and
    increasingly stretched away from it in both directions (so it can't be
    a log scale, which excludes zero/negative, but also isn't the uniformly
    spaced dial that the simple exp(c*x) model assumes). x=offset is where
    the dial is most compressed (the term vanishes there); n>1 controls how
    quickly it steepens away from that point.

    window_key: the nonlinear window-set input; may be zero or negative.
    ratio_keys: other inputs to fit as power terms, same as fit_power_law.
        Defaults to every other input column.

    Unlike fit_power_law, this is genuine nonlinear least squares (c, n,
    and offset all enter nonlinearly), not a closed-form regression -- it
    needs more samples to pin down reliably (3 extra free parameters) and
    can converge to a wrong local optimum without good coverage of the
    input's range on both sides of its true offset. Validate hard with
    holdout samples before trusting it, and prefer a plain lookup table
    over this if R^2 isn't convincingly close to 1.
    """
    if len(samples) < 2:
        raise ValueError("need at least 2 samples to fit anything")

    if ratio_keys is None:
        ratio_keys = [k for k in samples[0] if k not in (output_key, window_key)]

    for i, s in enumerate(samples):
        if s[output_key] <= 0:
            raise ValueError(f"sample {i} has non-positive {output_key}={s[output_key]!r}")
        for k in ratio_keys:
            if s[k] <= 0:
                raise ValueError(
                    f"sample {i} has non-positive {k}={s[k]!r} -- ratio inputs "
                    "must stay strictly positive; pass this key as window_key "
                    "instead if it can be zero or negative"
                )

    log_ratio_cols = {k: np.array([np.log(s[k]) for s in samples]) for k in ratio_keys}
    window_col = np.array([s[window_key] for s in samples], dtype=float)
    y = np.log(np.array([s[output_key] for s in samples]))
    n_ratio = len(ratio_keys)

    def model(_xdata: np.ndarray, *params: float) -> np.ndarray:
        log_k = params[0]
        exponents = params[1 : 1 + n_ratio]
        c, n, offset = params[1 + n_ratio :]
        value = np.full_like(y, log_k)
        for exp_val, k in zip(exponents, ratio_keys):
            value = value + exp_val * log_ratio_cols[k]
        shifted = window_col - offset
        value = value + c * np.sign(shifted) * np.abs(shifted) ** n
        return value

    p0 = [0.0] + [1.0] * n_ratio + [0.01, n_guess, 0.0]
    popt, _ = curve_fit(model, np.arange(len(samples)), y, p0=p0, maxfev=20000)

    log_k = float(popt[0])
    ratio_exponents = dict(zip(ratio_keys, popt[1 : 1 + n_ratio]))
    c, n, offset = (float(v) for v in popt[1 + n_ratio :])

    y_pred = model(np.arange(len(samples)), *popt)
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0

    return WindowFitResult(
        log_k=log_k,
        ratio_exponents=ratio_exponents,
        window_key=window_key,
        c=c,
        n=n,
        offset=offset,
        r_squared=r_squared,
        n_samples=len(samples),
    )


def validate(
    result: FitResult | WindowFitResult, holdout: list[dict[str, float]], output_key: str
) -> list[dict]:
    """Check the fitted formula against samples it was NOT fit on.

    Returns one report row per holdout sample: actual, predicted, and the
    relative error between them. Large errors here mean the relationship
    isn't a pure power law and needs a lookup table instead.
    """
    rows = []
    for sample in holdout:
        inputs = {k: v for k, v in sample.items() if k != output_key}
        actual = sample[output_key]
        predicted = result.predict(**inputs)
        rel_error = abs(predicted - actual) / actual if actual else float("inf")
        rows.append({
            "inputs": inputs,
            "actual": actual,
            "predicted": predicted,
            "rel_error": rel_error,
        })
    return rows


def load_samples(path: str) -> list[dict[str, float]]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return [{k: float(v) for k, v in row.items()} for row in reader]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", help="CSV of sample readings, one column per variable")
    parser.add_argument("--output", required=True, help="name of the output/dependent column")
    parser.add_argument(
        "--holdout", type=int, default=0,
        help="hold out the last N rows for validation instead of fitting on them",
    )
    parser.add_argument(
        "--linear", nargs="*", default=[],
        help="names of window-set input columns (e.g. an altitude dialed into a "
             "window) to fit as exp(c*x) instead of x^p; these may be zero or negative",
    )
    parser.add_argument(
        "--window",
        help="name of a window-set input column whose dial is visibly nonlinear "
             "(compressed near one point, stretched away from it) -- fit as "
             "exp(c * sign(x-offset) * |x-offset|^n) via nonlinear least squares "
             "instead of the closed-form --linear model. Mutually exclusive with --linear.",
    )
    parser.add_argument(
        "--fixed-exponent", nargs="*", default=[],
        help="pin a ratio input's exponent to a known value instead of fitting it, "
             "as NAME=VALUE (e.g. --fixed-exponent keas=2); use when a boundary "
             "condition or physical law proves the true exponent, so the regression "
             "doesn't spend that degree of freedom absorbing noise from elsewhere. "
             "Not compatible with --window.",
    )
    parser.add_argument(
        "--fixed-k", type=float,
        help="pin the leading constant k to a known value instead of fitting it "
             "(e.g. 1, when two quantities are defined to be equal at some "
             "reference point). Not compatible with --window.",
    )
    args = parser.parse_args()
    if args.window and args.linear:
        parser.error("--window and --linear are mutually exclusive")
    if args.window and (args.fixed_exponent or args.fixed_k is not None):
        parser.error("--window is not compatible with --fixed-exponent/--fixed-k")

    fixed_ratio_exponents = {}
    for item in args.fixed_exponent:
        name, sep, value = item.partition("=")
        if not sep:
            parser.error(f"--fixed-exponent expects NAME=VALUE, got {item!r}")
        fixed_ratio_exponents[name] = float(value)

    samples = load_samples(args.csv_path)
    if args.holdout:
        fit_samples, holdout_samples = samples[: -args.holdout], samples[-args.holdout :]
    else:
        fit_samples, holdout_samples = samples, []

    if args.window:
        result = fit_shifted_power_window(fit_samples, args.output, args.window)
    else:
        result = fit_power_law(
            fit_samples, args.output, linear_keys=set(args.linear),
            fixed_ratio_exponents=fixed_ratio_exponents or None,
            fixed_k=args.fixed_k,
        )
    print(f"fitted on {result.n_samples} samples")
    print(result.formula_str(args.output))
    print(f"R^2 = {result.r_squared:.6f}")
    if result.r_squared < 0.999:
        print("R^2 is not ~1.0 -- this is probably NOT a pure ratio relationship;")
        print("consider tabulating this step instead of using a formula.")

    if holdout_samples:
        print(f"\nvalidating against {len(holdout_samples)} held-out samples:")
        for row in validate(result, holdout_samples, args.output):
            print(
                f"  inputs={row['inputs']} actual={row['actual']:g} "
                f"predicted={row['predicted']:g} rel_error={row['rel_error']:.4%}"
            )


if __name__ == "__main__":
    main()

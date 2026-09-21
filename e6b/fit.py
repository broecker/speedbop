"""Derive power-law formulas from E6B slide-rule sample readings.

The E6B's calculator side is a proportion device: it multiplies and divides
by sliding log-scaled rings against each other, so most of what it computes
has the form

    output = k * input_1 ** p1 * input_2 ** p2 * ...

Taking logs turns that into a linear equation, so a handful of readings
taken directly off the device (vary one input at a time, across its full
range) is enough to recover k and each exponent exactly via least-squares
regression -- and R^2 tells you whether the relationship really is a clean
ratio or whether it's something else (additive correction, breakpoint
table, trig) that needs a lookup table instead.

Usage:
    python -m e6b.fit samples.csv --output tas --holdout 2
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import random

import numpy as np


@dataclass
class FitResult:
    k: float
    exponents: dict[str, float]
    r_squared: float
    n_samples: int

    def predict(self, **inputs: float) -> float:
        value = self.k
        for name, exp in self.exponents.items():
            value *= inputs[name] ** exp
        return value

    def formula_str(self, output_name: str = "output") -> str:
        terms = " * ".join(f"{name}^{exp:.4f}" for name, exp in self.exponents.items())
        return f"{output_name} = {self.k:.6g} * {terms}"


def fit_power_law(samples: list[dict[str, float]], output_key: str) -> FitResult:
    """Fit output = k * prod(input_i ** p_i) by linear regression in log space.

    samples: list of dicts, each mapping variable name -> value, all values
    strictly positive (log-log fitting requires it; the E6B's ratio side
    only ever handles positive quantities anyway).
    output_key: which field in each sample dict is the dependent variable.
    """
    if len(samples) < 2:
        raise ValueError("need at least 2 samples to fit anything")

    input_keys = [k for k in samples[0] if k != output_key]
    if not input_keys:
        raise ValueError("samples must include at least one input variable")

    X = np.array([[np.log(s[k]) for k in input_keys] for s in samples])
    y = np.array([np.log(s[output_key]) for s in samples])

    # augment with an intercept column to solve for log(k) alongside the exponents
    A = np.column_stack([X, np.ones(len(samples))])
    coeffs, *_ = np.linalg.lstsq(A, y, rcond=None)
    exponents = dict(zip(input_keys, coeffs[:-1]))
    k = float(np.exp(coeffs[-1]))

    y_pred = A @ coeffs
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0

    return FitResult(k=k, exponents=exponents, r_squared=r_squared, n_samples=len(samples))


def validate(result: FitResult, holdout: list[dict[str, float]], output_key: str) -> list[dict]:
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
    args = parser.parse_args()

    samples = load_samples(args.csv_path)
    if args.holdout:
        random.shuffle(samples)
        fit_samples, holdout_samples = samples[: -args.holdout], samples[-args.holdout :]
    else:
        fit_samples, holdout_samples = samples, []

    result = fit_power_law(fit_samples, args.output)
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

# speedbop
Automatic performance calculation for Birds of Prey

## E6B formula derivation

The game's performance calculations are done by hand with an E6B-style
ratio slide rule. Rather than modeling the device's geometry, we derive the
underlying formula for each calculation step from sample readings taken
directly off the device.

Most of what a slide rule computes is a proportion (`output = k * a^p * b^q
* ...`), which becomes linear once you take logs — so a few readings across
the full range of each input are enough to recover the exact formula via
least-squares regression, and R² tells you whether a given step really is a
clean ratio (fit it) or something else like an additive correction or a
breakpoint table (tabulate it instead).

```
python3 -m e6b.fit e6b/samples/example_tsd.csv --output time --holdout 2
```

To derive a real formula: take ~10 readings off the device for a given
calculation, varying one input at a time across its full range, save them
as a CSV (one column per variable, output column last), and run the command
above pointing at your file. `e6b/samples/example_tsd.csv` shows the format
using the classic time = distance / speed calculation.

### Ring-read vs. window-set inputs

Not every input is read the same way. Some are read directly off two
rotating log-scaled rings against each other (the classic multiply/divide
use of a slide rule) — those are fit as a power term (`value ** p`) and
must be strictly positive.

Others are dialed into a window to set a rotational *offset* between
rings, e.g. an altitude or temperature correction. Rotating a ring by an
amount proportional to a dialed value multiplies every subsequent reading
by `exp(c * value)`, not `value ** p` — and unlike a ring-read input, a
window-set one can legitimately be zero (dialing it to 0 just means "no
correction applied"). Pass those column names via `--linear`:

```
python3 -m e6b.fit e6b/samples/example_tas_window.csv --output tas --linear altitude_thousands --holdout 2
```

`e6b/samples/example_tas_window.csv` shows the format: a CAS/altitude/TAS
relationship where altitude is dialed into a window (including a `0`
row) and CAS is read off the rings directly.

### Nonlinear window dials

`--linear` assumes the window's dial is evenly spaced, so a fixed rotation
per unit of the dialed value. If the dial is visibly nonlinear instead —
compressed near one point and increasingly stretched away from it in both
directions, which can't be a log scale since it also has to represent zero
and negative values — fit it as `exp(c * sign(x-offset) * |x-offset| ** n)`
via `--window` instead:

```
python3 -m e6b.fit e6b/samples/example_tas_nonlinear_window.csv --output tas --window altitude --holdout 3
```

This is genuine nonlinear least squares (`c`, `n`, and `offset` all enter
nonlinearly, unlike every other fit here), so it needs more samples spread
across both sides of the dial's compressed point to pin down reliably, and
its R² is worth checking hard before trusting it — if it isn't convincingly
close to 1, tabulate the step instead rather than trusting a guessed curve
shape. `e6b/samples/example_tas_nonlinear_window.csv` shows the format,
including negative window values.

### Pinning known values instead of fitting them

If a boundary condition in the data proves an exponent or the leading
constant `k` exactly — e.g. two quantities are defined to be equal at some
reference point, as KEAS and KTAS are at sea level in `e6b/keas.csv` — pin
it instead of letting the regression fit it. Otherwise those degrees of
freedom just get spent absorbing noise from elsewhere in the data, which
can pull the fit away from a value you already know is correct:

```python
from e6b.fit import fit_power_law, load_samples

samples = load_samples("e6b/keas.csv")
result = fit_power_law(
    samples, output_key="ktas",
    linear_keys={"altitude"},
    fixed_ratio_exponents={"keas": 1.0},
    fixed_k=1.0,
)
# ktas = keas * exp(c * altitude), with c the only thing actually fit
```


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

A pinned exponent doesn't need a matching `fixed_k` if only the exponent
(not the constant) is physically known. `e6b/q.csv` is real dynamic
pressure data (`q`) against `keas`: physically, `q = 0.5*rho*V^2`, so the
`keas` exponent must be exactly 2, not just close to it:

```python
samples = load_samples("e6b/q.csv")
result = fit_power_law(samples, output_key="q", fixed_ratio_exponents={"keas": 2.0})
# q = k * keas^2, with k the only thing actually fit -- R^2 > 0.999,
# every point within ~1.5% except the smallest (a rounded integer reading)
```

### Quantities with no real-world meaning

Not everything on the device corresponds to a real quantity -- some
windows just display the result of composing two other dial settings, with
no external law to check the fit against. That doesn't change the
approach: the device is built from the same log-scaled rotating rings
either way, so the general unconstrained fit still applies, and R² plus
holdout validation are all you have to trust it (no physical sanity check
like `p=2` for dynamic pressure is available).

`e6b/smash.csv` is real data for one such quantity, read off a window
after two outer dials (`wl`, `q`) are set:

```
python3 -m e6b.fit e6b/smash.csv --output smash
# smash = 10 * wl^-1 * q^1, R^2 = 1.0 to floating-point precision
```

One row in the raw readings (`wl=40, q=100, smash=125`) was a confirmed
outlier -- off by -78% while every other row matched to floating-point
precision -- and was dropped rather than "corrected" to the formula's
predicted value, so this file contains only real device readings.

### Pinning from the CLI

`--fixed-exponent NAME=VALUE` (repeatable) and `--fixed-k VALUE` expose the
same pinning as `fixed_ratio_exponents`/`fixed_k` above, without needing a
Python script. `e6b/mach.csv` is real KEAS/altitude/Mach data -- KEAS is
proportional to Mach exactly (EAS depends on Mach and pressure altitude
only, confirmed by three separate altitude readings near 24-25 all giving
`keas/mach == 600` regardless of Mach), so the Mach exponent is pinned to
1 and only the altitude decay constant and `k` are fit:

```
python3 -m e6b.fit e6b/mach.csv --output keas --linear alt --fixed-exponent mach=1
# keas = 674.573 * mach^1 * exp(-0.0045*alt), R^2 = 0.999
```

The real atmosphere's barometric power law doesn't fit this data at all
(errors exceed 100% at high altitude) -- like the KTAS/KEAS relationship,
the game approximates it with a plain exponential decay instead of the
true physics formula.

## Reading 2D charts: e6b/chart.py

Not every calculation is a formula at all. Engine performance vs. altitude
and Mach, on some aircraft data cards, is given as a 2D chart: a family of
labeled contour lines ("isobars") showing curves of constant output.
There's no closed-form relationship to fit here -- the chart itself *is*
the data, and `e6b/chart.py` reads it the same way you'd read it by hand:

1. Slice every isobar at the query altitude -- interpolate along that
   isobar's own digitized points to find the Mach value it crosses there.
2. Interpolate across isobars by Mach -- sort the sliced points and
   interpolate the output value between the two that bracket the query.

```python
from e6b.chart import Isobar, IsobarChart

chart = IsobarChart([
    Isobar(output=50.0, altitude=[0, 150, 300], mach=[0.5, 0.8, 1.0]),
    Isobar(output=100.0, altitude=[0, 150, 300], mach=[0.9, 1.4, 2.0]),
])
chart.interpolate(altitude=75, mach=0.85)
```

Or from a CSV (`output,altitude,mach`, one row per digitized point, rows
sharing an `output` value forming one isobar) via `load_isobars(path)`.

This module rolls its own linear interpolation instead of using
`scipy.interpolate` -- a handful of digitized points and two 1D
interpolation passes don't need Delaunay triangulation, and it keeps this
part of the library dependency-free (no numpy either). Queries outside
the digitized range clamp to the nearest edge rather than extrapolating,
at both steps. Isobars must not cross (checked once at construction, by
sampling every isobar's altitude breakpoints rather than just the query
point or the chart's endpoints -- a crossing can happen strictly between
two breakpoints of a *different* isobar than the pair that crosses).

`e6b/engine.csv` is a real engine performance chart: 10 isobars (output
30-75) over altitude 0-310 and Mach 0-2.5. The crossing check caught a
real transcription error on the first pass -- isobars 65 and 70 crossed
around altitude=245 and ended up swapped by altitude=310 (a 43% gap, not
a rounding-level discrepancy), traced to a single mis-transcribed point
(output=70's altitude=310 reading) and corrected against the chart.


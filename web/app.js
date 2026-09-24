// Pyodide bootstrap + UI wiring for the speedbop turn calculator.
//
// speedbop.py and chart.py run completely unmodified inside Pyodide --
// their only imports are stdlib (dataclasses, json, math, pathlib, csv), so
// nothing needs to be transpiled or polyfilled. Both live flat at the repo
// root (not inside the e6b/ formula-derivation toolkit, which needs
// numpy/scipy and is never imported by speedbop.py), so no subdirectory is
// needed for our own source -- only the aircraft data cards under adc/
// need one. This file's only job is to get those files into Pyodide's
// virtual filesystem, then wire the DOM to the resulting Python objects.

const PY_SOURCE_FILES = ["speedbop.py", "chart.py"];

// pyodide.FS.mkdirTree/writeFile resolve a RELATIVE path against the
// filesystem root ("/"), not against FS.cwd() ("/home/pyodide") -- despite
// FS.cwd() reporting /home/pyodide, mkdirTree("adc") silently creates
// "/adc", and any subsequent relative write into "adc/..." then fails with
// ErrnoError (ENOENT), because that directory doesn't exist relative to
// wherever writeFile resolves it either. Passing absolute paths sidesteps
// the whole inconsistency.
const FS_ROOT = "/home/pyodide";

let pyodide;
let speedbop;
let pathlib;
let history; // PerformanceHistory PyProxy, set once setup completes
let aircraftManifest = [];

function setStatus(text) {
  document.getElementById("loading-status").textContent = text;
}

function showFatalError(err) {
  console.error(err);
  const el = document.getElementById("loading-error");
  el.textContent = "Failed to start: " + (err && err.message ? err.message : err);
  el.classList.remove("hidden");
}

function showScreen(id) {
  for (const el of document.querySelectorAll(".screen")) {
    el.classList.add("hidden");
  }
  document.getElementById("loading").classList.add("hidden");
  document.getElementById(id).classList.remove("hidden");
}

// ---------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------

async function fetchText(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`could not fetch ${path} (${response.status})`);
  }
  return response.text();
}

async function writeFile(path, contents) {
  const absPath = `${FS_ROOT}/${path}`;
  const dir = absPath.split("/").slice(0, -1).join("/");
  pyodide.FS.mkdirTree(dir);
  pyodide.FS.writeFile(absPath, contents);
}

async function loadAircraftFiles(entry) {
  // Fetches an aircraft's data card JSON, then reads *its own*
  // dry_engine_output/ab_engine_output fields to find and fetch the
  // matching isobar CSVs -- the manifest doesn't duplicate those filenames,
  // so there's nothing to keep in sync between the files. ab_engine_output
  // is optional (only afterburning aircraft have one).
  const jsonText = await fetchText(`adc/${entry.path}`);
  await writeFile(`adc/${entry.path}`, jsonText);

  const adcDict = JSON.parse(jsonText);
  for (const key of ["dry_engine_output", "ab_engine_output"]) {
    const chartPath = adcDict[key];
    if (chartPath) {
      const csvText = await fetchText(`adc/${chartPath}`);
      await writeFile(`adc/${chartPath}`, csvText);
    }
  }
}

async function boot() {
  setStatus("Loading Python runtime…");
  pyodide = await loadPyodide();

  setStatus("Loading speedbop…");
  for (const path of PY_SOURCE_FILES) {
    await writeFile(path, await fetchText(path));
  }
  speedbop = pyodide.pyimport("speedbop");
  pathlib = pyodide.pyimport("pathlib");

  setStatus("Loading aircraft roster…");
  aircraftManifest = JSON.parse(await fetchText("adc/index.json"));
  for (const entry of aircraftManifest) {
    await loadAircraftFiles(entry);
  }

  populateAircraftSelect(document.getElementById("aircraft-select"));
  populateAircraftSelect(document.getElementById("performance-aircraft-select"));
  showScreen("setup-screen");
}

// ---------------------------------------------------------------------
// Setup screen
// ---------------------------------------------------------------------

function populateAircraftSelect(select) {
  select.innerHTML = "";
  for (const entry of aircraftManifest) {
    const option = document.createElement("option");
    option.value = entry.path;
    option.textContent = `${entry.name} (${entry.version})`;
    select.appendChild(option);
  }
}

function currentAircraftEntry() {
  const path = document.getElementById("aircraft-select").value;
  return aircraftManifest.find((entry) => entry.path === path);
}

document.getElementById("setup-form").addEventListener("submit", (event) => {
  event.preventDefault();

  const path = document.getElementById("aircraft-select").value;
  const weight = parseFloat(document.getElementById("weight-input").value);
  const ktas = parseFloat(document.getElementById("ktas-input").value);
  const altitude = parseInt(document.getElementById("altitude-input").value, 10);

  const adcPath = pathlib.Path(`adc/${path}`);
  const adc = speedbop.AircraftDataCard.from_json(adcPath);
  const state = speedbop.AircraftState(adc, weight, ktas, altitude);
  history = speedbop.PerformanceHistory(state);

  document.getElementById("turn-aircraft-name").textContent = currentAircraftEntry().name;
  document.getElementById("history-list").innerHTML = "";
  setupAfterburnerToggle(state);
  renderState(history.current_state);
  showScreen("turn-screen");
});

// ---------------------------------------------------------------------
// Aircraft Performance screen: sustained turn reference (no active game)
// ---------------------------------------------------------------------

const PERFORMANCE_KTAS_MIN = 120; // 3 FP -- nothing slower is worth showing
const PERFORMANCE_KTAS_STEP = 10;
const PERFORMANCE_KTAS_MAX = 800;
const PERFORMANCE_LOAD_CAP = 36; // 12 Gs -- keeps the chart zoomed on the realistic range

function performanceAircraftEntry() {
  const path = document.getElementById("performance-aircraft-select").value;
  return aircraftManifest.find((entry) => entry.path === path);
}

// Weight and the afterburner toggle are aircraft-specific, so a freshly
// picked aircraft resets both -- weight to its own combat weight, AB
// toggle to on-if-available -- rather than carrying over the previous
// aircraft's values.
function initPerformanceAircraftFields() {
  const entry = performanceAircraftEntry();
  const adc = speedbop.AircraftDataCard.from_json(pathlib.Path(`adc/${entry.path}`));
  document.getElementById("performance-weight-input").value = adc.stores.combat_weight;
  updateAfterburnerAvailability(
    adc,
    document.getElementById("performance-afterburner-toggle"),
    document.getElementById("performance-afterburner-caption")
  );
}

function updatePerformanceScreen() {
  const entry = performanceAircraftEntry();
  const weight = parseFloat(document.getElementById("performance-weight-input").value);
  const altitude = parseInt(document.getElementById("performance-altitude-input").value, 10);
  if (Number.isNaN(weight) || Number.isNaN(altitude)) return; // mid-edit, e.g. field just cleared

  const adc = speedbop.AircraftDataCard.from_json(pathlib.Path(`adc/${entry.path}`));
  const toggle = document.getElementById("performance-afterburner-toggle");
  const afterburner = !toggle.disabled && toggle.checked;

  // 120% of this airframe's own safe load reads better than a flat cap for
  // most aircraft -- 36 loads (12G) only kicks in as a ceiling for one with
  // an unusually high safe load.
  const loadCap = Math.min(1.2 * adc.characteristics.combat_safe_load, PERFORMANCE_LOAD_CAP);

  const ktasValues = [];
  for (let ktas = PERFORMANCE_KTAS_MIN; ktas <= PERFORMANCE_KTAS_MAX; ktas += PERFORMANCE_KTAS_STEP) {
    ktasValues.push(ktas);
  }

  const profile = speedbop.sustained_turn_profile(adc, weight, altitude, ktasValues, afterburner);
  const points = profile.toJs({ dict_converter: Object.fromEntries });
  // Pass the original PyProxy list straight into another Python call rather
  // than the already-.toJs()'d copy -- Pyodide hands it back to Python as
  // the same underlying list, no reconversion needed.
  const bestProxy = speedbop.find_best_sustained_turn(profile);
  const best = bestProxy
    ? { ktas: bestProxy.ktas, load: bestProxy.load, phadCells: bestProxy.phad_cells }
    : null;

  const modeLabel = afterburner ? "AB" : "dry";
  document.getElementById("performance-chart-caption").textContent = best
    ? `${entry.name} @ ${altitude} alt, ${modeLabel} power -- corner: ` +
      `${Math.round(best.ktas)} kt / ${round1(best.load)} loads / ${round1(best.phadCells)} PHAD cells`
    : `${entry.name} @ ${altitude} alt, ${modeLabel} power -- no sustained-turn crossing in range`;

  renderSustainedTurnChart(document.getElementById("performance-chart"), points, best, loadCap);
}

function renderSustainedTurnChart(container, points, best, loadCap) {
  const width = 300;
  const height = 200;
  const padLeft = 28;
  const padRight = 10;
  const padTop = 10;
  const padBottom = 20;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const ktasMin = points[0].ktas;
  const ktasMax = Math.max(...points.map((p) => p.ktas)) * 1.02;

  const x = (ktas) => padLeft + ((ktas - ktasMin) / (ktasMax - ktasMin)) * plotW;
  const y = (load) => padTop + plotH - (Math.min(load, loadCap) / loadCap) * plotH;

  // PHAD cells is a completely different, much smaller-magnitude unit than
  // loads (single digits vs. tens) -- sharing the loads axis would squash
  // this line flat against the bottom, so it gets its own right-side scale
  // instead, independent of loadCap.
  const cellsMax = Math.max(1, Math.max(...points.map((p) => p.max_pullable_cells)) * 1.1);
  const y2 = (cells) => padTop + plotH - (Math.min(cells, cellsMax) / cellsMax) * plotH;

  const pathFor = (getLoad) =>
    points.map((p) => `${x(p.ktas).toFixed(1)},${y(getLoad(p)).toFixed(1)}`).join(" ");
  const cellsPath = points
    .map((p) => `${x(p.ktas).toFixed(1)},${y2(p.max_pullable_cells).toFixed(1)}`)
    .join(" ");

  const axes = `
    <line class="axis-line" x1="${padLeft}" y1="${padTop}" x2="${padLeft}" y2="${padTop + plotH}"></line>
    <line class="axis-line" x1="${padLeft}" y1="${padTop + plotH}" x2="${padLeft + plotW}" y2="${padTop + plotH}"></line>
    <line class="axis-line cells-axis-line" x1="${padLeft + plotW}" y1="${padTop}" x2="${padLeft + plotW}" y2="${padTop + plotH}"></line>
    <text x="${padLeft}" y="${height - 4}">${Math.round(ktasMin)} kt</text>
    <text x="${padLeft + plotW}" y="${height - 4}" text-anchor="end">${Math.round(ktasMax)} kt</text>
    <text x="2" y="${padTop + plotH}">0</text>
    <text x="2" y="${padTop + 6}">${round1(loadCap)} loads (${round1(loadCap / 3)}G)</text>
    <text class="cells-axis-label" x="${(padLeft + plotW + 2).toFixed(1)}" y="${padTop + plotH}">0</text>
    <text class="cells-axis-label" x="${(padLeft + plotW + 2).toFixed(1)}" y="${padTop + 6}">${Math.ceil(cellsMax)} cells</text>
  `;

  // Only label the best point if it actually falls within the capped
  // display range -- a crossing above the cap isn't in the window this
  // chart is deliberately zoomed to.
  let bestMarker = "";
  if (best && best.load <= loadCap) {
    const bx = x(best.ktas);
    const by = y(best.load);
    bestMarker =
      `<circle class="best-turn-point" cx="${bx.toFixed(1)}" cy="${by.toFixed(1)}" r="3.5"></circle>` +
      `<text class="best-turn-label" x="${bx.toFixed(1)}" y="${(by - 7).toFixed(1)}" text-anchor="middle">` +
      `Corner: ${Math.round(best.ktas)}kt / ${round1(best.load)} loads</text>`;
  }

  container.innerHTML =
    `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">` +
    axes +
    `<polyline class="structural-line" points="${pathFor((p) => p.max_load)}"></polyline>` +
    `<polyline class="sustained-line" points="${pathFor((p) => p.sustained_load)}"></polyline>` +
    `<polyline class="cells-line" points="${cellsPath}"></polyline>` +
    bestMarker +
    `<rect class="hover-target" x="${padLeft}" y="${padTop}" width="${plotW}" height="${plotH}"></rect>` +
    `<g class="crosshair hidden">` +
    `<line class="crosshair-line" x1="0" y1="${padTop}" x2="0" y2="${padTop + plotH}"></line>` +
    `<circle class="crosshair-dot sustained-dot" r="2.6"></circle>` +
    `<circle class="crosshair-dot structural-dot" r="2.6"></circle>` +
    `<circle class="crosshair-dot cells-dot" r="2.6"></circle>` +
    `</g>` +
    `</svg>`;

  wireSustainedTurnChartInteractivity(container, points, { x, y, y2, width, padLeft, plotW, ktasMin, ktasMax });
}

function wireSustainedTurnChartInteractivity(container, points, scale) {
  const svg = container.querySelector("svg");
  const hoverTarget = container.querySelector(".hover-target");
  const crosshair = container.querySelector(".crosshair");
  const crosshairLine = container.querySelector(".crosshair-line");
  const sustainedDot = container.querySelector(".sustained-dot");
  const structuralDot = container.querySelector(".structural-dot");
  const cellsDot = container.querySelector(".cells-dot");
  const tooltip = document.getElementById("performance-chart-tooltip");

  function nearestPoint(event) {
    const rect = svg.getBoundingClientRect();
    const svgX = ((event.clientX - rect.left) / rect.width) * scale.width;
    const frac = Math.min(Math.max((svgX - scale.padLeft) / scale.plotW, 0), 1);
    const ktas = scale.ktasMin + frac * (scale.ktasMax - scale.ktasMin);
    const index = Math.min(
      points.length - 1,
      Math.max(0, Math.round((ktas - scale.ktasMin) / PERFORMANCE_KTAS_STEP))
    );
    return points[index];
  }

  function showTooltip(event) {
    const p = nearestPoint(event);
    const px = scale.x(p.ktas);

    crosshair.classList.remove("hidden");
    crosshairLine.setAttribute("x1", px.toFixed(1));
    crosshairLine.setAttribute("x2", px.toFixed(1));
    sustainedDot.setAttribute("cx", px.toFixed(1));
    sustainedDot.setAttribute("cy", scale.y(p.sustained_load).toFixed(1));
    structuralDot.setAttribute("cx", px.toFixed(1));
    structuralDot.setAttribute("cy", scale.y(p.max_load).toFixed(1));
    cellsDot.setAttribute("cx", px.toFixed(1));
    cellsDot.setAttribute("cy", scale.y2(p.max_pullable_cells).toFixed(1));

    tooltip.innerHTML =
      `<strong>${Math.round(p.ktas)} kt (${p.speed_fp} FP)</strong>` +
      `<span class="tt-sustained">Sustained: ${round1(p.sustained_load)} loads ` +
      `(${round1(p.sustained_phad_cells)} cells)</span><br>` +
      `<span class="tt-structural">Structural: ${p.max_load} loads ` +
      `(${round1(p.max_phad_cells)} cells)</span><br>` +
      `<span class="tt-cells">Turn rate: ${p.max_pullable_cells} PHAD cells</span>`;
    tooltip.classList.remove("hidden");

    // Flip sides so the tooltip never overflows the chart's edge.
    const rightHalf = px > scale.width / 2;
    tooltip.style.left = rightHalf ? "" : `${(px / scale.width) * 100}%`;
    tooltip.style.right = rightHalf ? `${((scale.width - px) / scale.width) * 100}%` : "";
  }

  function hideTooltip(event) {
    // Touch has no real "hover" -- lifting the finger fires pointerleave
    // too, and hiding right then would make the tooltip flash and vanish
    // before it could be read. Leave it up until a different point is
    // touched instead; only a real mouse pointer clears it on leave.
    if (event && event.pointerType === "touch") return;
    crosshair.classList.add("hidden");
    tooltip.classList.add("hidden");
  }

  hoverTarget.addEventListener("pointermove", showTooltip);
  hoverTarget.addEventListener("pointerdown", showTooltip);
  hoverTarget.addEventListener("pointerleave", hideTooltip);
}

// "Back" returns to whichever screen the player actually came from --
// landing back on the setup form after opening this mid-game from the
// turn screen would look like the game had been reset, when the
// in-progress PerformanceHistory is untouched the whole time.
let performanceScreenOrigin = "setup-screen";

function setPerformanceScreenOrigin(origin) {
  performanceScreenOrigin = origin;
  document.getElementById("close-performance-screen-btn").textContent =
    origin === "turn-screen" ? "← Back to turn" : "← Back to setup";
}

document.getElementById("open-performance-screen-btn").addEventListener("click", () => {
  setPerformanceScreenOrigin("setup-screen");
  initPerformanceAircraftFields();
  showScreen("performance-screen");
  updatePerformanceScreen();
});

document.getElementById("open-performance-screen-from-turn-btn").addEventListener("click", () => {
  setPerformanceScreenOrigin("turn-screen");

  // Prefill with the aircraft/state actually being played, not the
  // screen's own generic defaults -- this is a reference for the
  // player's current situation, not a fresh lookup.
  const entry = currentAircraftEntry();
  const state = history.current_state;
  const adc = speedbop.AircraftDataCard.from_json(pathlib.Path(`adc/${entry.path}`));

  document.getElementById("performance-aircraft-select").value = entry.path;
  document.getElementById("performance-weight-input").value = state.weight;
  document.getElementById("performance-altitude-input").value = state.altitude;
  updateAfterburnerAvailability(
    adc,
    document.getElementById("performance-afterburner-toggle"),
    document.getElementById("performance-afterburner-caption")
  );
  // Match the turn screen's own current AB mode rather than always
  // resetting to "on by default", so the chart reflects how they're
  // actually flying right now.
  const turnToggle = document.getElementById("afterburner-toggle");
  if (!turnToggle.disabled) {
    document.getElementById("performance-afterburner-toggle").checked = turnToggle.checked;
  }

  showScreen("performance-screen");
  updatePerformanceScreen();
});

document.getElementById("close-performance-screen-btn").addEventListener("click", () => {
  showScreen(performanceScreenOrigin);
});

document.getElementById("performance-aircraft-select").addEventListener("change", () => {
  initPerformanceAircraftFields();
  updatePerformanceScreen();
});

document.getElementById("performance-weight-input").addEventListener("input", updatePerformanceScreen);
document.getElementById("performance-altitude-input").addEventListener("input", updatePerformanceScreen);
document.getElementById("performance-afterburner-toggle").addEventListener("change", updatePerformanceScreen);

// ---------------------------------------------------------------------
// Turn screen: steppers
// ---------------------------------------------------------------------

function initSteppers() {
  for (const stepper of document.querySelectorAll(".stepper")) {
    const output = stepper.querySelector("output");

    for (const button of stepper.querySelectorAll("button")) {
      button.addEventListener("click", () => {
        // Read min/max fresh from the dataset on every click, not just at
        // init -- the pulls stepper's max changes turn to turn (see
        // setMaxPulls), so a value captured once here would go stale.
        const min = Number(stepper.dataset.min);
        const max = Number(stepper.dataset.max);
        const value = Number(output.textContent) + Number(button.dataset.step);
        output.textContent = String(Math.min(max, Math.max(min, value)));
        if (stepper.dataset.stepper === "pulls") updatePullsWarning();
      });
    }
  }
}

function stepperValue(name) {
  const output = document.querySelector(`.stepper[data-stepper="${name}"] output`);
  return Number(output.textContent);
}

// ---------------------------------------------------------------------
// Turn screen: afterburner toggle
// ---------------------------------------------------------------------

// Shared by the turn screen and the performance screen.
function updateAfterburnerAvailability(adc, toggle, captionEl) {
  const hasAfterburner = !!adc.ab_engine_output;
  toggle.disabled = !hasAfterburner;
  toggle.checked = hasAfterburner; // on by default when the aircraft has one
  captionEl.textContent = hasAfterburner ? "" : "This aircraft has no afterburner.";
}

// Set once per aircraft (on setup, not on every turn) -- otherwise a
// mid-flight "switch to dry for the rest of this run" choice would get
// silently reset back to on after every resolved turn.
function setupAfterburnerToggle(state) {
  updateAfterburnerAvailability(
    state.adc,
    document.getElementById("afterburner-toggle"),
    document.getElementById("afterburner-caption")
  );
}

function afterburnerEnabled() {
  const toggle = document.getElementById("afterburner-toggle");
  return !toggle.disabled && toggle.checked;
}

document.getElementById("afterburner-toggle").addEventListener("change", () => {
  // Only the max-output hint and chart depend on the toggle directly; the
  // engine-output field's typed value is left alone so flipping the toggle
  // to compare modes doesn't clobber whatever the player already entered.
  updateEngineMaxDisplays(history.current_state);
});

// ---------------------------------------------------------------------
// Turn screen: max pulls (structural load limit)
// ---------------------------------------------------------------------

function setMaxPulls(maxPulls) {
  const stepper = document.querySelector('.stepper[data-stepper="pulls"]');
  stepper.dataset.max = maxPulls;
  document.getElementById("max-pulls-hint").textContent = `Max: ${maxPulls}`;

  // The limit can drop between turns (e.g. slower speed -> lower max load)
  // below whatever the stepper was still showing from the turn before.
  const output = stepper.querySelector("output");
  if (Number(output.textContent) > maxPulls) {
    output.textContent = String(maxPulls);
  }
  updatePullsWarning();
}

function updatePullsWarning() {
  const stepper = document.querySelector('.stepper[data-stepper="pulls"]');
  const maxPulls = Number(stepper.dataset.max);
  const pulls = Number(stepper.querySelector("output").textContent);
  const warningEl = document.getElementById("pulls-warning");

  const headroom = maxPulls - pulls;
  if (pulls > 0 && headroom >= 0 && headroom <= 2) {
    warningEl.textContent = `⚠ Close to max load -- ${maxPulls} pulls available`;
    warningEl.classList.remove("hidden");
  } else {
    warningEl.classList.add("hidden");
  }
}

// ---------------------------------------------------------------------
// Turn screen: rendering
// ---------------------------------------------------------------------

function renderState(state) {
  const fp = speedbop.speed_fp_from_ktas(state.ktas);
  document.getElementById("stat-speed").textContent = `${Math.round(state.ktas)} / ${fp}`;
  document.getElementById("stat-altitude").textContent = state.altitude;
  document.getElementById("stat-mach").textContent = state.get_mach();

  // A red status bar is meant to grab the eye at the one moment it matters --
  // about to stall out (FP too low to maneuver) or about to hit the ground.
  const critical = fp < 2 || state.altitude < 5;
  document.querySelector(".state-bar").classList.toggle("danger", critical);

  setMaxPulls(state.get_max_load());
  setDefaultEngineOutputValue(state);
  updateEngineMaxDisplays(state);
}

// Sticky by default: once a turn has resolved, the next turn's default is
// whatever engine output actually got used last turn (itself possibly an
// override), not always back to max -- matches how a throttle setting
// tends to persist turn to turn unless deliberately changed.
function setDefaultEngineOutputValue(state) {
  const lastTurn = history.turns.length > 0 ? history.turns[history.turns.length - 1] : null;
  const defaultValue = lastTurn
    ? lastTurn.engine_delta_ktas
    : state.get_engine_output(afterburnerEnabled());
  document.getElementById("engine-output-input").value = round1(defaultValue);
}

// Unlike setDefaultEngineOutputValue(), this depends only on the current
// toggle state, not on turn history -- so it's also what the toggle's own
// change listener calls, without touching whatever the player has typed.
function updateEngineMaxDisplays(state) {
  const afterburner = afterburnerEnabled();
  const maxOutput = state.get_engine_output(afterburner);
  document.getElementById("max-engine-hint").textContent = `Max: ${maxOutput}`;
  renderEngineChart(state, afterburner);
}

// ---------------------------------------------------------------------
// Turn screen: engine output chart
// ---------------------------------------------------------------------

function renderEngineChart(state, afterburner) {
  const usingAb = afterburner && !!state.adc.ab_engine_output;
  const chart = usingAb ? state.adc.ab_engine_output : state.adc.dry_engine_output;
  const rows = chart.to_rows().toJs({ dict_converter: Object.fromEntries });
  const currentAltitude = state.altitude;
  const currentMach = state.get_mach();
  const maxOutput = state.get_engine_output(afterburner);

  document.getElementById("engine-chart-caption").textContent =
    `${usingAb ? "AB" : "Dry"} chart -- current point: ${currentMach} mach @ ` +
    `${currentAltitude} alt → max output ${maxOutput}`;
  document.getElementById("engine-chart").innerHTML =
    buildEngineChartSvg(rows, currentAltitude, currentMach);
}

function buildEngineChartSvg(rows, currentAltitude, currentMach) {
  const width = 300;
  const height = 200;
  const padLeft = 28;
  const padRight = 10;
  const padTop = 10;
  const padBottom = 20;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  // Domain is the chart's own digitized range only -- IsobarChart.interpolate()
  // clamps an out-of-range query to the nearest edge rather than extrapolating,
  // so the marker below is clamped the same way rather than stretching the
  // axes to fit it (which would misleadingly suggest the chart extends there).
  const rawMachMax = Math.max(...rows.map((r) => r.mach));
  const rawAltMax = Math.max(...rows.map((r) => r.altitude));
  const machMax = rawMachMax * 1.05;
  const altMax = rawAltMax * 1.02;

  const x = (mach) => padLeft + (mach / machMax) * plotW;
  const y = (altitude) => padTop + plotH - (altitude / altMax) * plotH;

  const markerMach = Math.min(Math.max(currentMach, 0), rawMachMax);
  const markerAltitude = Math.min(Math.max(currentAltitude, 0), rawAltMax);

  const byOutput = new Map();
  for (const row of rows) {
    if (!byOutput.has(row.output)) byOutput.set(row.output, []);
    byOutput.get(row.output).push(row);
  }
  const outputs = [...byOutput.keys()].sort((a, b) => a - b);
  const outputMin = outputs[0];
  const outputMax = outputs[outputs.length - 1];

  let isobars = "";
  for (const output of outputs) {
    const points = byOutput.get(output).slice().sort((a, b) => a.altitude - b.altitude);
    const frac = outputMax === outputMin ? 0 : (output - outputMin) / (outputMax - outputMin);
    const hue = 210 - frac * 200; // low output = blue, high output = red
    const path = points.map((p) => `${x(p.mach).toFixed(1)},${y(p.altitude).toFixed(1)}`).join(" ");
    const last = points[points.length - 1];
    isobars += `<polyline class="isobar-line" points="${path}" style="stroke: hsl(${hue} 70% 50%)"></polyline>`;
    isobars += `<text class="isobar-label" x="${(x(last.mach) + 2).toFixed(1)}" y="${y(last.altitude).toFixed(1)}" style="fill: hsl(${hue} 70% 40%)">${output}</text>`;
  }

  const axes = `
    <line class="axis-line" x1="${padLeft}" y1="${padTop}" x2="${padLeft}" y2="${padTop + plotH}"></line>
    <line class="axis-line" x1="${padLeft}" y1="${padTop + plotH}" x2="${padLeft + plotW}" y2="${padTop + plotH}"></line>
    <text x="${padLeft}" y="${height - 4}">0</text>
    <text x="${padLeft + plotW}" y="${height - 4}" text-anchor="end">${machMax.toFixed(1)} mach</text>
    <text x="2" y="${padTop + plotH}">0</text>
    <text x="2" y="${padTop + 6}">${Math.round(altMax)} alt</text>
  `;

  const currentPoint =
    `<circle class="current-point" cx="${x(markerMach).toFixed(1)}" ` +
    `cy="${y(markerAltitude).toFixed(1)}" r="3.5"></circle>`;

  return (
    `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">` +
    `${axes}${isobars}${currentPoint}</svg>`
  );
}

function renderBreakdown(performance) {
  const alphaNearLimit = performance.max_alpha - performance.alpha <= 3;

  const rows = [
    ["Pulls", `${performance.segment_pulls} (${performance.gs} Gs)`, false],
    ["Segment length", `${performance.segment_fp} / ${performance.initial_speed_fp}`, false],
    ["Δ Altitude", performance.delta_altitude, false],
    ["Alpha", `${round1(performance.alpha)} / ${round1(performance.max_alpha)}`, alphaNearLimit],
    ["Induced ΔKTAS", round1(performance.induced_delta_ktas), false],
    ["Gravity ΔKTAS", round1(performance.gravity_delta_ktas), false],
    ["Form ΔKTAS", round1(performance.form_delta_ktas), false],
    ["Engine ΔKTAS", `${round1(performance.engine_delta_ktas)} (${performance.afterburner ? "AB" : "dry"})`, false],
    ["New speed", `${Math.round(performance.new_state.ktas)} (${performance.new_speed_fp} FP)`, false],
  ];

  const tbody = document.querySelector("#breakdown-table tbody");
  tbody.innerHTML = "";
  for (const [label, value, warn] of rows) {
    const tr = document.createElement("tr");
    if (warn) tr.classList.add("near-limit");
    tr.innerHTML = `<td>${label}</td><td>${value}</td>`;
    tbody.appendChild(tr);
  }
  document.getElementById("breakdown-panel").open = true;
}

function addHistoryRow(turnNumber, performance) {
  const li = document.createElement("li");
  li.innerHTML = `
    <span class="turn-no">#${turnNumber}</span>
    <span>
      ${Math.round(performance.new_state.ktas)} KTAS @ ${performance.new_state.altitude}
      <div class="turn-detail">${performance.segment_pulls} pulls, Δalt ${performance.delta_altitude}</div>
    </span>
  `;
  document.getElementById("history-list").prepend(li);
}

function round1(x) {
  return Math.round(x * 10) / 10;
}

document.getElementById("resolve-turn-btn").addEventListener("click", () => {
  const pulls = stepperValue("pulls");
  const deltaAltitude = stepperValue("delta-altitude");
  const segmentFpRaw = document.getElementById("segment-fp-input").value;
  const segmentFp = segmentFpRaw === "" ? null : parseInt(segmentFpRaw, 10);
  const engineOutputRaw = document.getElementById("engine-output-input").value;
  const engineOutput = engineOutputRaw === "" ? null : parseFloat(engineOutputRaw);
  const afterburner = afterburnerEnabled();

  const performance = history.resolve_turn(pulls, segmentFp, deltaAltitude, engineOutput, afterburner);

  renderState(history.current_state);
  renderBreakdown(performance);
  addHistoryRow(history.turns.length, performance);
});

// ---------------------------------------------------------------------

initSteppers();
boot().catch(showFatalError);

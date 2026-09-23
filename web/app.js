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
  // dry_engine_output field to find and fetch the matching isobar CSV --
  // the manifest doesn't duplicate that filename, so there's nothing to
  // keep in sync between the two files.
  const jsonText = await fetchText(`adc/${entry.path}`);
  await writeFile(`adc/${entry.path}`, jsonText);

  const adcDict = JSON.parse(jsonText);
  const chartPath = adcDict.dry_engine_output;
  if (chartPath) {
    const csvText = await fetchText(`adc/${chartPath}`);
    await writeFile(`adc/${chartPath}`, csvText);
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

  populateAircraftPicker();
  showScreen("setup-screen");
}

// ---------------------------------------------------------------------
// Setup screen
// ---------------------------------------------------------------------

function populateAircraftPicker() {
  const select = document.getElementById("aircraft-select");
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
  renderState(history.current_state);
  showScreen("turn-screen");
});

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
  setDefaultEngineOutput(state);
  renderEngineChart(state);
}

// Sticky by default: once a turn has resolved, the next turn's default is
// whatever engine output actually got used last turn (itself possibly an
// override), not always back to max -- matches how a throttle setting
// tends to persist turn to turn unless deliberately changed.
function setDefaultEngineOutput(state) {
  const maxOutput = state.get_engine_output();
  const lastTurn = history.turns.length > 0 ? history.turns[history.turns.length - 1] : null;
  const defaultValue = lastTurn ? lastTurn.engine_delta_ktas : maxOutput;

  document.getElementById("engine-output-input").value = round1(defaultValue);
  document.getElementById("max-engine-hint").textContent = `Max: ${maxOutput}`;
}

// ---------------------------------------------------------------------
// Turn screen: engine output chart
// ---------------------------------------------------------------------

function renderEngineChart(state) {
  const rows = state.adc.dry_engine_output.to_rows().toJs({ dict_converter: Object.fromEntries });
  const currentAltitude = state.altitude;
  const currentMach = state.get_mach();
  const maxOutput = state.get_engine_output();

  document.getElementById("engine-chart-caption").textContent =
    `Current point: ${currentMach} mach @ ${currentAltitude} alt → max output ${maxOutput}`;
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
    ["Engine ΔKTAS", round1(performance.engine_delta_ktas), false],
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

  const performance = history.resolve_turn(pulls, segmentFp, deltaAltitude, engineOutput);

  renderState(history.current_state);
  renderBreakdown(performance);
  addHistoryRow(history.turns.length, performance);
});

// ---------------------------------------------------------------------

initSteppers();
boot().catch(showFatalError);

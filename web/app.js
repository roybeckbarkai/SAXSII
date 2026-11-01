/* SAXSII browser frontend. Talks to server.py over a small JSON API. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const fmt = (v, d = 4) => (v === null || v === undefined || !isFinite(v)) ? "–" :
  (Math.abs(v) >= 1e4 || (Math.abs(v) < 1e-3 && v !== 0)) ? v.toExponential(3) : (+v).toFixed(d).replace(/\.?0+$/, "");
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = {
  st: null,                 // last /api/state payload
  files: [],                // tree from the server
  rows: [],                 // flattened, filtered file rows: {id, name, child, header}
  sel: new Set(), anchor: null, current: null,
  curveSel: new Set(), curveAnchor: null,
  tab: "image",
  pick: null,               // null | center | qmin | qmax | marker
  render: null,             // last 2D render response
  rings: [],
  renderSeq: 0,
  trackTimer: null,
};

const PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#7f7f7f", "#bcbd22"];
const CMAPS = ["jet", "viridis", "inferno", "magma", "plasma", "cividis", "turbo", "gray", "gray_r", "RdBu_r", "coolwarm", "seismic", "bwr"];

// ----------------------------------------------------------------------------- plumbing

async function api(url, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(url, opts);
  let data;
  try { data = await res.json(); } catch { data = { error: `${res.status} ${res.statusText}` }; }
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

let toastTimer;
function toast(msg, kind = "error") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (kind === "info" ? " info" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), kind === "info" ? 3000 : 7000);
}

function status(msg, busy = false) {
  const s = $("#status");
  s.textContent = msg || "";
  s.classList.toggle("busy", busy);
}

/** Run an async action with a busy status line; errors become a toast. */
async function run(label, fn) {
  status(label + "…", true);
  try {
    const out = await fn();
    status("");
    return out;
  } catch (e) {
    status("");
    toast(e.message);
    console.error(e);
  }
}

const unitFactor = () => S.st?.unit_factor ?? 1;
const unitLabel = () => S.st ? S.st.units[S.st.settings.unit] : "q";
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function baseLayout() {
  return {
    paper_bgcolor: cssVar("--plot-bg"), plot_bgcolor: cssVar("--plot-bg"),
    font: { color: cssVar("--text"), family: "Segoe UI, system-ui, sans-serif", size: 12 },
    margin: { l: 60, r: 20, t: 20, b: 45 },
  };
}
const PLOT_CONFIG = { responsive: true, displaylogo: false, scrollZoom: true, modeBarButtonsToRemove: ["select2d", "lasso2d"] };

// ----------------------------------------------------------------------------- state -> UI

function applyState(st) {
  S.st = st;
  const s = st.settings;
  const f = st.unit_factor;
  $("#folder-input").value ||= s.folder || "";
  $("#poni-input").value = s.poni || "";
  $("#mask-input").value = s.mask || "";
  $("#bins").value = s.bins;
  $("#sigma").value = s.sigma_thres;
  const unit = $("#unit");
  if (!unit.options.length) unit.innerHTML = Object.entries(st.units).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  unit.value = s.unit;
  $("#qmin").value = s.q_min == null ? "" : +(s.q_min * f).toPrecision(6);
  $("#qmax").value = s.q_max == null ? "" : +(s.q_max * f).toPrecision(6);
  $("#marker-q").placeholder = unitLabel();

  const c = st.calibration;
  for (const [id, key] of [["#cal-dist", "distance_mm"], ["#cal-wl", "wavelength_A"], ["#cal-cx", "center_x"], ["#cal-cy", "center_y"]]) {
    $(id).value = c ? +(+c[key]).toFixed(key === "wavelength_A" ? 6 : 3) : "";
    $(id).disabled = !c;
  }
  $("#cal-extra").textContent = c ? `${c.detector} · pixel ${c.pixel_x_um}×${c.pixel_y_um} µm · tilt ${fmt(c.tilt, 3)}°` : "Load a PONI file to see the geometry.";
  $("#cal-apply").disabled = $("#cal-reset").disabled = !c;

  renderCurveList(st.curves);
  renderMarkers(st.markers);
}

async function refreshState() {
  applyState(await api("/api/state"));
}

// ----------------------------------------------------------------------------- file list

function setFiles(files) {
  S.files = files;
  const ids = new Set(files.flatMap(f => f.children ? f.children.map(c => c.id) : [f.id]));
  for (const id of [...S.sel]) if (!ids.has(id)) S.sel.delete(id);
  renderFileList();
}

function renderFileList() {
  const q = $("#file-filter").value.trim().toLowerCase();
  S.rows = [];
  for (const f of S.files) {
    if (q && !f.name.toLowerCase().includes(q)) continue;
    if (f.children) {
      S.rows.push({ id: f.id, name: f.name, header: true });
      for (const c of f.children) S.rows.push({ id: c.id, name: c.name, child: true });
    } else S.rows.push({ id: f.id, name: f.name });
  }
  const ul = $("#file-list");
  if (!S.rows.length) {
    ul.innerHTML = `<li class="empty">${S.files.length ? "No match" : "No images"}</li>`;
    return;
  }
  ul.innerHTML = S.rows.map((r, i) => r.header
    ? `<li class="empty" title="${esc(r.name)}">${esc(r.name)}</li>`
    : `<li data-i="${i}" class="${r.child ? "child" : ""}${S.sel.has(r.id) ? " selected" : ""}${S.current === r.id ? " current" : ""}" title="${esc(r.id)}">${esc(r.name)}</li>`).join("");
}

function leafIds() { return S.rows.filter(r => !r.header).map(r => r.id); }

function onListClick(e, rows, selSet, anchorKey, idOf) {
  const li = e.target.closest("li[data-i]");
  if (!li) return null;
  const i = +li.dataset.i;
  const id = idOf(rows[i]);
  if (e.shiftKey && S[anchorKey] !== null) {
    const ids = rows.map(idOf);
    const a = ids.indexOf(S[anchorKey]);
    const [lo, hi] = a < i ? [a, i] : [i, a];
    if (!e.ctrlKey && !e.metaKey) selSet.clear();
    for (let k = lo; k <= hi; k++) if (ids[k] !== null) selSet.add(ids[k]);
  } else if (e.ctrlKey || e.metaKey) {
    selSet.has(id) ? selSet.delete(id) : selSet.add(id);
    S[anchorKey] = id;
  } else {
    selSet.clear();
    selSet.add(id);
    S[anchorKey] = id;
  }
  return id;
}

$("#file-list").addEventListener("click", e => {
  const id = onListClick(e, S.rows, S.sel, "anchor", r => r.header ? null : r.id);
  if (id === null) return;
  S.current = id;
  renderFileList();
  if (S.tab === "curves") switchTab("image");
  else showView();
});
$("#file-list").addEventListener("keydown", e => {
  if (!["ArrowDown", "ArrowUp"].includes(e.key)) return;
  e.preventDefault();
  const ids = leafIds();
  const i = ids.indexOf(S.current) + (e.key === "ArrowDown" ? 1 : -1);
  if (i < 0 || i >= ids.length) return;
  S.current = S.anchor = ids[i];
  S.sel = new Set([ids[i]]);
  renderFileList();
  $("#file-list li.current")?.scrollIntoView({ block: "nearest" });
  showView();
});
$("#file-filter").addEventListener("input", renderFileList);

async function openFolder(path) {
  const r = await api("/api/folder", { path });
  $("#folder-input").value = r.folder;
  $("#folder-label").textContent = r.folder;
  S.sel.clear(); S.current = null;
  setFiles(r.files);
  if (S.trackTimer) startTracking();
}
$("#folder-open").onclick = () => run("Opening folder", () => openFolder($("#folder-input").value));
$("#folder-input").addEventListener("keydown", e => e.key === "Enter" && $("#folder-open").click());

// folder tracking (polling - works for network drives too)
function startTracking() {
  stopTracking();
  let known = new Set(S.files.map(f => f.id + ":" + (f.children?.length ?? 0)));
  S.trackTimer = setInterval(async () => {
    try {
      const r = await api("/api/files");
      const now = r.files.map(f => f.id + ":" + (f.children?.length ?? 0));
      if (now.length === known.size && now.every(k => known.has(k))) return;
      const fresh = r.files.filter(f => !known.has(f.id + ":" + (f.children?.length ?? 0)));
      known = new Set(now);
      setFiles(r.files);
      if ($("#track-show").checked && fresh.length) {
        const newest = fresh.sort((a, b) => b.mtime - a.mtime)[0];
        const id = newest.children ? newest.children.at(-1).id : newest.id;
        S.current = S.anchor = id; S.sel = new Set([id]);
        renderFileList();
        showView();
      }
    } catch (e) { /* folder may be briefly unavailable */ }
  }, 2000);
}
function stopTracking() { clearInterval(S.trackTimer); S.trackTimer = null; }
$("#track").onchange = e => {
  $("#track-show").disabled = !e.target.checked;
  e.target.checked ? startTracking() : stopTracking();
};

// ----------------------------------------------------------------------------- calibration / settings

$("#poni-load").onclick = () => run("Loading PONI", async () => {
  applyState(await api("/api/poni", { path: $("#poni-input").value }));
  toast("PONI loaded", "info");
  showView();
});
$("#mask-load").onclick = () => run("Loading mask", async () => {
  applyState(await api("/api/mask", { path: $("#mask-input").value }));
  toast($("#mask-input").value ? "Mask loaded" : "Mask cleared", "info");
  showView();
});
$("#nxs-load").onclick = async () => {
  const path = await browse({ title: "Choose NeXus file", exts: ".nxs,.h5", path: S.st?.settings.folder || "" });
  if (!path) return;
  run("Converting NeXus file (may take a while)", async () => {
    const r = await api("/api/nxs", { path });
    applyState(r);
    $("#folder-input").value = $("#folder-label").textContent = r.settings.folder;
    S.sel.clear(); S.current = null;
    setFiles(r.files);
    if (r.warning) toast(r.warning); else toast("NeXus file converted and loaded", "info");
    showView();
  });
};
$("#calib-tool").onclick = () => run("Starting pyFAI-calib2", () => api("/api/calib_tool", {}));

async function postSettings(patch) {
  applyState(await api("/api/settings", patch));
}
$("#bins").onchange = e => run("Saving", () => postSettings({ bins: e.target.value }));
$("#sigma").onchange = e => run("Saving", () => postSettings({ sigma_thres: e.target.value }));
$("#unit").onchange = e => run("Saving", async () => { await postSettings({ unit: e.target.value }); showView(); });
for (const id of ["qmin", "qmax"]) {
  $("#" + id).onchange = e => run("Saving", async () => {
    await postSettings({ [id === "qmin" ? "q_min" : "q_max"]: e.target.value });
    await rangeChanged();
  });
}
$("#qrange-clear").onclick = () => run("Saving", async () => { await postSettings({ q_min: null, q_max: null }); await rangeChanged(); });

async function rangeChanged() {
  if (S.tab === "image") await drawRings();
  else showView();
}

$("#cal-apply").onclick = () => run("Updating geometry", async () => {
  applyState(await api("/api/calibration", {
    distance_mm: $("#cal-dist").value, wavelength_A: $("#cal-wl").value,
    center_x: $("#cal-cx").value, center_y: $("#cal-cy").value,
  }));
  showView();
});
$("#cal-reset").onclick = () => run("Reloading PONI", async () => { applyState(await api("/api/calibration/reload", {})); showView(); });

// ----------------------------------------------------------------------------- tabs & views

function switchTab(tab) {
  S.tab = tab;
  $$(".tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  $("#toolbar-2d").classList.toggle("hidden", tab === "curves");
  $("#toolbar-1d").classList.toggle("hidden", tab !== "curves");
  $$(".for-image").forEach(el => el.classList.toggle("hidden", tab !== "image"));
  $$(".for-anis").forEach(el => el.classList.toggle("hidden", tab !== "anis"));
  $("#chi-wrap").classList.toggle("hidden", tab !== "anis");
  if (tab === "anis") {
    if (!$("#cmap").dataset.userSet) $("#cmap").value = "RdBu_r";
  } else if (!$("#cmap").dataset.userSet) $("#cmap").value = "jet";
  $("#scale").closest("label").classList.toggle("hidden", tab === "anis");  // signed maps are always linear
  setPick(null);
  showView();
}
$$(".tabs button").forEach(b => (b.onclick = () => switchTab(b.dataset.tab)));

$("#cmap").innerHTML = CMAPS.map(c => `<option>${c}</option>`).join("");
$("#cmap").value = "jet";
$("#cmap").onchange = e => { e.target.dataset.userSet = "1"; showView(); };
for (const id of ["#scale", "#lo-pct", "#hi-pct", "#anis-mode", "#anis-sa", "#anis-pol"]) $(id).onchange = () => showView();
$("#show-mask").onchange = $("#show-rings").onchange = () => S.render && draw2D(S.render);
for (const id of ["#logx", "#logy", "#errbars"]) $(id).onchange = () => showView();

function showPlaceholder(on, text) {
  $("#placeholder").classList.toggle("hidden", !on);
  if (text) $("#placeholder p").textContent = text;
  if (on) { Plotly.purge($("#plot")); delete $("#plot").dataset.bound; S.render = null; }
}

function showView() {
  if (S.tab === "curves") return plotCurves();
  if (!S.current) return showPlaceholder(true, S.files.length ? "Select an image." : "Open an image folder and a PONI file to start.");
  return render2D();
}

function renderRequest() {
  const view = S.tab === "image" ? "raw" : S.tab === "cake" ? "cake" : $("#anis-mode").value;
  return {
    id: S.current, view, cmap: $("#cmap").value,
    scale: S.tab === "anis" ? "linear" : $("#scale").value,
    lo_pct: $("#lo-pct").value, hi_pct: $("#hi-pct").value,
    solid_angle: $("#anis-sa").checked, polarization: $("#anis-pol").value,
  };
}

async function render2D() {
  const seq = ++S.renderSeq;
  const req = renderRequest();
  const label = { raw: "Loading image", cake: "Caking", subtract: "Computing anisotropy", divide: "Computing anisotropy" }[req.view];
  await run(label, async () => {
    const r = await api("/api/render", req);
    if (seq !== S.renderSeq) return;  // a newer request superseded this one
    if (req.view === "raw") S.rings = (await api("/api/rings")).rings;
    draw2D(r);
    if (S.tab === "anis") await chiProfile();
  });
}

function overlayShapes() {
  const c = S.st?.calibration;
  if (!c || !$("#show-rings").checked) return { shapes: [], annotations: [] };
  const line = { color: "#ffffff", width: 1.5 };
  const shapes = [
    { type: "line", x0: c.center_x - 12, x1: c.center_x + 12, y0: c.center_y, y1: c.center_y, line },
    { type: "line", x0: c.center_x, x1: c.center_x, y0: c.center_y - 12, y1: c.center_y + 12, line },
  ];
  const annotations = [];
  for (const ring of S.rings) {
    if (!isFinite(ring.r)) continue;
    const color = ring.kind === "range" ? "#ffffff" : "#ffeb3b";
    shapes.push({ type: "circle", x0: c.center_x - ring.r, x1: c.center_x + ring.r, y0: c.center_y - ring.r, y1: c.center_y + ring.r,
      line: { color, width: 1.5, dash: ring.kind === "range" ? "dash" : "solid" } });
    annotations.push({ x: c.center_x + ring.r * 0.7071, y: c.center_y + ring.r * 0.7071, text: esc(ring.label), showarrow: false,
      font: { color, size: 11 }, xanchor: "left", yanchor: "bottom" });
  }
  return { shapes, annotations };
}

function draw2D(r) {
  S.render = r;
  showPlaceholder(false);
  const [h, w] = r.shape;
  const isCake = r.view === "cake";
  const ax = isCake ? r.axes : { x0: 0.5, dx: 1, y0: 0.5, dy: 1 };  // pixel i is centred on i + 0.5
  const traces = [{ type: "image", source: r.png, x0: ax.x0, dx: ax.dx, y0: ax.y0, dy: ax.dy, hoverinfo: "x+y", name: "" }];
  if (r.mask_png && $("#show-mask").checked) traces.push({ type: "image", source: r.mask_png, x0: 0.5, dx: 1, y0: 0.5, dy: 1, hoverinfo: "skip" });
  const layout = Object.assign(baseLayout(), {
    xaxis: { title: isCake ? unitLabel() : "x (px)", showgrid: false, zeroline: false, constrain: "domain" },
    yaxis: isCake ? { title: "χ (°)", showgrid: false, zeroline: false, autorange: true, scaleanchor: false }
      : { title: "y (px)", showgrid: false, zeroline: false, autorange: true, scaleanchor: "x", constrain: "domain" },
    uirevision: `${r.view === "cake" ? "cake" : "det"}:${w}x${h}`,
    dragmode: "pan",
    hovermode: "closest",
  });
  if (r.view === "raw") Object.assign(layout, overlayShapes());
  const range = r.view === "subtract" || r.view === "divide"
    ? `color range ±${fmt(r.vmax)} · ${(100 * r.valid_fraction).toFixed(0)}% of pixels valid`
    : `color range ${fmt(r.vmin)} – ${fmt(r.vmax)} (${r.scale})`;
  layout.title = { text: `<span style="font-size:12px">${esc(r.id)} · ${range}</span>`, x: 0.01, xanchor: "left", y: 0.99 };
  layout.margin.t = 30;
  Plotly.react($("#plot"), traces, layout, PLOT_CONFIG);
  bindPlotEvents();
}

async function drawRings() {
  if (S.tab !== "image" || !S.render) return;
  S.rings = (await api("/api/rings")).rings;
  Plotly.relayout($("#plot"), overlayShapes());
}

// ----------------------------------------------------------------------------- 1D curves

function curveColor(name) {
  const i = (S.st?.curves || []).indexOf(name);
  return PALETTE[(i < 0 ? 0 : i) % PALETTE.length];
}

function renderCurveList(names) {
  for (const n of [...S.curveSel]) if (!names.includes(n)) S.curveSel.delete(n);
  const ul = $("#curve-list");
  ul.innerHTML = names.length
    ? names.map((n, i) => `<li data-i="${i}" class="${S.curveSel.has(n) ? "selected" : ""}" title="${esc(n)}"><span class="swatch" style="background:${curveColor(n)}"></span>${esc(n)}</li>`).join("")
    : `<li class="empty">Integrate images to add curves</li>`;
}

$("#curve-list").addEventListener("click", e => {
  const names = S.st.curves;
  const id = onListClick(e, names, S.curveSel, "curveAnchor", n => n);
  if (id === null) return;
  renderCurveList(names);
  if (S.tab !== "curves") switchTab("curves"); else plotCurves();
});

async function plotCurves() {
  const names = S.curveSel.size ? [...S.curveSel] : (S.st?.curves || []);
  if (!names.length) return showPlaceholder(true, "No integrated curves yet. Select images and press “Integrate selected”.");
  await run("Plotting", async () => {
    const r = await api("/api/curves", { names });
    showPlaceholder(false);
    const traces = r.curves.map(c => ({
      type: "scattergl", mode: "lines", name: c.name, x: c.x, y: c.y,
      line: { color: curveColor(c.name), width: 1.6 },
      error_y: $("#errbars").checked ? { type: "data", array: c.sigma, visible: true, thickness: 0.6, width: 0 } : undefined,
    }));
    const f = unitFactor();
    const shapes = [], annotations = [];
    for (const [name, q] of Object.entries(S.st.markers)) {
      shapes.push({ type: "line", xref: "x", yref: "paper", x0: q * f, x1: q * f, y0: 0, y1: 1, line: { color: "#f9a825", width: 1, dash: "dot" } });
      annotations.push({ x: q * f, y: 1, xref: "x", yref: "paper", text: esc(name), showarrow: false, yanchor: "bottom", font: { size: 11 } });
    }
    for (const [key, label] of [["q_min", "q min"], ["q_max", "q max"]]) {
      const q = S.st.settings[key];
      if (q != null) shapes.push({ type: "line", xref: "x", yref: "paper", x0: q * f, x1: q * f, y0: 0, y1: 1, line: { color: cssVar("--muted"), width: 1, dash: "dash" } });
    }
    const layout = Object.assign(baseLayout(), {
      xaxis: { title: r.label, type: $("#logx").checked ? "log" : "linear", gridcolor: cssVar("--border"), zeroline: false },
      yaxis: { title: "Intensity", type: $("#logy").checked ? "log" : "linear", gridcolor: cssVar("--border"), exponentformat: "power", zeroline: false },
      legend: { orientation: "h", y: 1.02, yanchor: "bottom" },
      uirevision: "curves", hovermode: "closest", dragmode: "zoom", shapes, annotations,
    });
    layout.margin.t = 40;
    Plotly.react($("#plot"), traces, layout, PLOT_CONFIG);
    bindPlotEvents();
  });
}

$("#curve-remove").onclick = () => run("Removing", async () => {
  if (!S.curveSel.size) throw new Error("Select curves to remove.");
  const r = await api("/api/curves/remove", { names: [...S.curveSel] });
  S.curveSel.clear(); S.st.curves = r.curves; renderCurveList(r.curves); showView();
});
$("#curve-clear").onclick = () => run("Clearing", async () => {
  if (!S.st.curves.length || !confirm("Remove all curves from the list? (.dat files on disk are kept)")) return;
  const r = await api("/api/curves/remove", {});
  S.curveSel.clear(); S.st.curves = r.curves; renderCurveList(r.curves); showView();
});
$("#curve-export").onclick = () => run("Exporting", async () => {
  const r = await api("/api/curves/export", { names: [...S.curveSel] });
  toast(`Saved ${r.paths.length} CSV file(s) to the image folder`, "info");
});
$("#curve-load").onclick = async () => {
  const files = await browse({ title: "Load .dat curves", exts: ".dat", multi: true, path: S.st.settings.folder, lockDir: true });
  if (!files) return;
  run("Loading curves", async () => {
    const r = await api("/api/curves/load_dat", { files });
    S.st.curves = r.curves; renderCurveList(r.curves);
    files.forEach(f => S.curveSel.add(f.replace(/\.dat$/i, "")));
    switchTab("curves");
  });
};

$("#curve-subtract").onclick = async () => {
  const names = S.st.curves;
  if (names.length < 2) return toast("Integrate at least two curves first.");
  const sel = [...S.curveSel];
  const opts = v => names.map(n => `<option ${n === v ? "selected" : ""}>${esc(n)}</option>`).join("");
  const v = await dialog("Subtract curves", `
    <label class="field">Sample<select name="sample">${opts(sel[0] ?? names[0])}</select></label>
    <label class="field">Background<select name="background">${opts(sel[1] ?? names[1])}</select></label>
    <label class="field">Background scale factor<input name="factor" type="number" step="any" value="1"></label>
    <label class="field">New curve name<input name="name" required></label>`,
    form => { if (!form.name.value) form.name.value = `${form.sample.value} - ${form.background.value}`; });
  if (!v) return;
  run("Subtracting", async () => {
    const r = await api("/api/curves/subtract", v);
    S.st.curves = r.curves; S.curveSel = new Set([r.name, v.sample, v.background]);
    renderCurveList(r.curves); switchTab("curves");
  });
};

$("#curve-average").onclick = async () => {
  if (S.curveSel.size < 2) return toast("Select at least two curves to average.");
  const v = await dialog("Average curves", `<p class="muted">${S.curveSel.size} curves, interpolated onto the q grid of the first.</p>
    <label class="field">New curve name<input name="name" value="Average" required></label>`);
  if (!v) return;
  run("Averaging", async () => {
    const r = await api("/api/curves/average", { names: [...S.curveSel], name: v.name });
    S.st.curves = r.curves; S.curveSel.add(r.name); renderCurveList(r.curves); showView();
  });
};

// ----------------------------------------------------------------------------- integration

async function integrate(ids) {
  if (!ids.length) return toast("Select images to integrate.");
  const prog = $("#progress");
  prog.hidden = false; prog.max = ids.length; prog.value = 0;
  const done = [];
  try {
    for (const id of ids) {
      status(`Integrating ${id} (${done.length + 1}/${ids.length})…`, true);
      try {
        const c = await api("/api/integrate", { id });
        done.push(c.name);
      } catch (e) {
        toast(`${id}: ${e.message}`);
        if (/PONI|folder/.test(e.message)) break;
      }
      prog.value = done.length;
    }
  } finally {
    prog.hidden = true;
    status("");
    if (done.length) toast(`Integrated ${done.length} image(s); .dat files saved to the image folder`, "info");
  }
  await refreshState();
  if (done.length) {
    S.curveSel = new Set(done.slice(-12));
    renderCurveList(S.st.curves);
    switchTab("curves");
  }
}
$("#integrate-sel").onclick = () => integrate([...S.sel]);
$("#integrate-all").onclick = () => integrate(leafIds());

// ----------------------------------------------------------------------------- 2D image arithmetic

$("#img-average").onclick = async () => {
  if (S.sel.size < 2) return toast("Select at least two images.");
  const v = await dialog("Average images", `<p class="muted">${S.sel.size} images → new float32 TIFF in the folder.</p>
    <label class="field">File name<input name="name" value="Average.tif" required></label>`);
  if (!v) return;
  run("Averaging images", async () => {
    const r = await api("/api/images/average", { ids: [...S.sel], name: v.name });
    setFiles(r.files); toast(`Saved ${r.path}`, "info");
  });
};
$("#img-subtract").onclick = async () => {
  const sel = [...S.sel];
  if (sel.length !== 2) return toast("Select exactly two images (sample and background).");
  const v = await dialog("Subtract images", `
    <label class="field">Sample<select name="a">${sel.map(i => `<option>${esc(i)}</option>`).join("")}</select></label>
    <label class="field">Background<select name="b">${sel.reverse().map(i => `<option>${esc(i)}</option>`).join("")}</select></label>
    <label class="field">Background scale factor<input name="factor" type="number" step="any" value="1"></label>
    <label class="field">File name<input name="name" value="Subtracted.tif" required></label>`);
  if (!v) return;
  run("Subtracting images", async () => {
    const r = await api("/api/images/subtract", v);
    setFiles(r.files); toast(`Saved ${r.path}`, "info");
  });
};

// ----------------------------------------------------------------------------- anisotropy chi profile

async function chiProfile() {
  const s = S.st.settings;
  const plot = $("#chi-plot");
  if (s.q_min == null || s.q_max == null) {
    Plotly.purge(plot);
    plot.innerHTML = `<p class="muted" style="padding:10px">Set q min and q max (e.g. around a peak) to get I(χ), its anisotropy amplitude and orientation.</p>`;
    $("#chi-metrics").textContent = "";
    return;
  }
  const r = await api("/api/chi_profile", { id: S.current, solid_angle: $("#anis-sa").checked, polarization: $("#anis-pol").value });
  const m = r.metrics;
  $("#chi-metrics").innerHTML = `<span title="Fit I(χ) = I₀[1 + A cos 2(χ − φ)]">A = <b>${fmt(m.amplitude, 3)}</b> · φ = <b>${fmt(m.orientation_deg, 1)}°</b></span>
    · max/min ${fmt(m.max_over_min, 2)} · ${(100 * m.coverage).toFixed(0)}% χ coverage`;
  plot.innerHTML = "";
  const f = unitFactor();
  const layout = Object.assign(baseLayout(), {
    margin: { l: 60, r: 20, t: 6, b: 36 },
    xaxis: { title: "χ (°)", range: [-180, 180], dtick: 45, gridcolor: cssVar("--border"), zeroline: false },
    yaxis: { title: "I", gridcolor: cssVar("--border"), zeroline: false },
    showlegend: false,
    shapes: [{ type: "line", x0: m.orientation_deg, x1: m.orientation_deg, yref: "paper", y0: 0, y1: 1, line: { color: "#d62728", dash: "dot", width: 1 } },
      { type: "line", x0: m.orientation_deg - 180 * Math.sign(m.orientation_deg || 1), x1: m.orientation_deg - 180 * Math.sign(m.orientation_deg || 1), yref: "paper", y0: 0, y1: 1, line: { color: "#d62728", dash: "dot", width: 1 } }],
  });
  Plotly.react(plot, [{ type: "scatter", mode: "lines+markers", x: r.chi, y: r.intensity, marker: { size: 3 }, line: { width: 1.2, color: PALETTE[0] },
    name: `${fmt(s.q_min * f)}–${fmt(s.q_max * f)}` }], layout, { responsive: true, displaylogo: false });
}

// ----------------------------------------------------------------------------- picking & hover

function setPick(mode) {
  S.pick = S.pick === mode ? null : mode;
  $$("[data-pick]").forEach(b => b.classList.toggle("active-pick", b.dataset.pick === S.pick));
  const hints = { center: "Click the beam center", qmin: "Click on the image / curve at q min", qmax: "Click on the image / curve at q max", marker: "Click where to put the marker" };
  status(S.pick ? hints[S.pick] + " (Esc to cancel)" : "");
  const dl = $("#plot .nsewdrag");
  if (dl) dl.style.cursor = S.pick ? "crosshair" : "";
}
$$("[data-pick]").forEach(b => (b.onclick = () => {
  if (b.dataset.pick !== "marker" && S.tab === "curves" && b.dataset.pick === "center") return;
  if (S.tab !== "curves" && !S.render) return toast("Show an image first.");
  setPick(b.dataset.pick);
}));
document.addEventListener("keydown", e => { if (e.key === "Escape" && S.pick) setPick(null); });

/** q (display unit) at a plot click, for any tab. */
async function qAtClick(pt) {
  if (S.tab === "curves" || S.tab === "cake") return pt.x;
  return (await api("/api/pick_q", { id: S.current, x: pt.x - 0.5, y: pt.y - 0.5 })).q;
}

async function onPlotClick(ev) {
  if (!S.pick || !ev.points?.length) return;
  const pt = ev.points[0];
  const mode = S.pick;
  setPick(null);
  if (mode === "center") {
    if (S.tab !== "image") return;
    await run("Setting center", async () => {
      applyState(await api("/api/calibration", { center_x: pt.x, center_y: pt.y }));
      await drawRings();
    });
  } else if (mode === "qmin" || mode === "qmax") {
    await run("Setting q range", async () => {
      const q = await qAtClick(pt);
      await postSettings({ [mode === "qmin" ? "q_min" : "q_max"]: +q.toPrecision(6) });
      await rangeChanged();
    });
  } else if (mode === "marker") {
    const q = await run("Reading q", () => qAtClick(pt));
    if (q == null) return;
    const v = await dialog("Add marker", `<p class="muted">at ${fmt(q)} ${esc(unitLabel())}</p><label class="field">Name<input name="name" required></label>`);
    if (!v) return;
    await run("Adding marker", async () => { await api("/api/markers", { name: v.name, q }); await refreshState(); await rangeChanged(); });
  }
}

let hoverTimer, hoverSeq = 0;
function onPlotHover(ev) {
  const pt = ev.points?.[0];
  if (!pt) return;
  const ro = $("#readout");
  if (S.tab === "curves") {
    ro.innerHTML = `${esc(pt.data.name)} · ${esc(unitLabel())} <b>${fmt(pt.x)}</b> · I <b>${fmt(pt.y)}</b>`;
    return;
  }
  if (S.tab === "cake") {
    ro.innerHTML = `${esc(unitLabel())} <b>${fmt(pt.x)}</b> · χ <b>${fmt(pt.y, 1)}°</b>`;
    return;
  }
  clearTimeout(hoverTimer);
  const seq = ++hoverSeq;
  hoverTimer = setTimeout(async () => {
    try {
      const p = await api(`/api/pixel?id=${encodeURIComponent(S.current)}&x=${pt.x - 0.5}&y=${pt.y - 0.5}`);
      if (seq !== hoverSeq || !p.inside) return;
      let html = `x <b>${p.x}</b> · y <b>${p.y}</b> · I <b>${fmt(p.value)}</b>`;
      if (p.q !== undefined) html += ` · r <b>${fmt(p.r, 1)}</b> px · ${esc(unitLabel())} <b>${fmt(p.q)}</b> · χ <b>${fmt(p.chi, 1)}°</b>`;
      if (p.masked) html += ` · <b>masked</b>`;
      ro.innerHTML = html;
    } catch { /* ignore */ }
  }, 60);
}

function bindPlotEvents() {
  const el = $("#plot");
  if (el.dataset.bound) return;
  el.dataset.bound = "1";
  el.on("plotly_click", onPlotClick);
  el.on("plotly_hover", onPlotHover);
}

// ----------------------------------------------------------------------------- markers list

function renderMarkers(markers) {
  const f = unitFactor();
  const entries = Object.entries(markers);
  $("#marker-list").innerHTML = entries.length
    ? entries.map(([n, q]) => `<li data-name="${esc(n)}">${esc(n)} <span class="muted">${fmt(q * f)}</span><span class="x" title="Remove">✕</span></li>`).join("")
    : `<li class="empty">No markers</li>`;
}
$("#marker-list").addEventListener("click", e => {
  const li = e.target.closest("li[data-name]");
  if (!li || !e.target.classList.contains("x")) return;
  run("Removing marker", async () => { await api("/api/markers", { remove: li.dataset.name }); await refreshState(); await rangeChanged(); });
});
$("#marker-add").onclick = () => run("Adding marker", async () => {
  await api("/api/markers", { name: $("#marker-name").value, q: $("#marker-q").value });
  $("#marker-name").value = $("#marker-q").value = "";
  await refreshState(); await rangeChanged();
});
$("#marker-clear").onclick = () => run("Clearing markers", async () => { await api("/api/markers", { clear: true }); await refreshState(); await rangeChanged(); });

// ----------------------------------------------------------------------------- dialogs & file browser

/** Show a form dialog; resolves to {field: value} or null when cancelled. */
function dialog(title, html, prepare) {
  const dlg = $("#modal");
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = html;
  const form = $("#modal-form");
  prepare?.(form);
  dlg.showModal();
  form.querySelector("input:not([type=hidden]), select")?.focus();
  return new Promise(resolve => {
    dlg.addEventListener("close", () => {
      if (dlg.returnValue !== "ok") return resolve(null);
      resolve(Object.fromEntries(new FormData(form)));
    }, { once: true });
  });
}

/** Server-side file browser. Resolves to a path (or list of names when multi) or null. */
function browse({ title, exts = "", dirMode = false, multi = false, path = "", lockDir = false }) {
  return new Promise(resolve => {
    const dlg = $("#modal");
    $("#modal-title").textContent = title;
    const body = $("#modal-body");
    body.innerHTML = `<div class="browser"><div class="row"><select id="br-drives" class="${lockDir ? "hidden" : ""}"></select>
      <button type="button" id="br-up" class="${lockDir ? "hidden" : ""}">↑ Up</button></div>
      <div class="browser-path" id="br-path"></div><ul class="list" id="br-list"></ul>
      ${dirMode ? `<p class="muted small">Navigate into the folder and press OK.</p>` : ""}</div>`;
    let cur = null, chosen = new Set();
    async function go(p) {
      try {
        const r = await api(`/api/browse?path=${encodeURIComponent(p || "")}&exts=${encodeURIComponent(exts)}`);
        cur = r; chosen.clear();
        $("#br-path").textContent = r.path;
        $("#br-drives").innerHTML = r.drives.map(d => `<option ${r.path.toUpperCase().startsWith(d.toUpperCase()) ? "selected" : ""}>${d}</option>`).join("");
        $("#br-drives").classList.toggle("hidden", lockDir || !r.drives.length);
        $("#br-list").innerHTML = (lockDir ? [] : r.dirs.map(d => `<li class="dir" data-d="${esc(d)}">${esc(d)}</li>`))
          .concat(dirMode ? [] : r.files.map(f => `<li class="file" data-f="${esc(f)}">${esc(f)}</li>`)).join("")
          || `<li class="empty">Empty</li>`;
      } catch (e) { toast(e.message); }
    }
    const sep = () => (cur.path.includes("\\") ? "\\" : "/");
    const join = (a, b) => a.replace(/[\\/]+$/, "") + sep() + b;
    $("#br-up").onclick = () => cur?.parent && go(cur.parent);
    $("#br-drives").onchange = e => go(e.target.value);
    $("#br-list").onclick = e => {
      const li = e.target.closest("li");
      if (!li) return;
      if (li.dataset.d !== undefined) return go(join(cur.path, li.dataset.d));
      if (li.dataset.f === undefined) return;
      if (!multi) chosen.clear();
      chosen.has(li.dataset.f) ? chosen.delete(li.dataset.f) : chosen.add(li.dataset.f);
      $$("#br-list li").forEach(x => x.classList.toggle("selected", chosen.has(x.dataset.f)));
    };
    $("#br-list").ondblclick = e => {
      const li = e.target.closest("li[data-f]");
      if (li && !multi) { chosen = new Set([li.dataset.f]); dlg.close("ok"); }
    };
    dlg.addEventListener("close", () => {
      if (dlg.returnValue !== "ok" || !cur) return resolve(null);
      if (dirMode) return resolve(cur.path);
      if (!chosen.size) return resolve(null);
      resolve(multi ? [...chosen] : join(cur.path, [...chosen][0]));
    }, { once: true });
    dlg.showModal();
    go(path);
  });
}

const BROWSE = {
  folder: { title: "Choose image folder", dirMode: true, input: "#folder-input", after: () => $("#folder-open").click() },
  poni: { title: "Choose PONI file", exts: ".poni", input: "#poni-input", after: () => $("#poni-load").click() },
  mask: { title: "Choose mask file", exts: ".msk,.edf,.tif,.tiff", input: "#mask-input", after: () => $("#mask-load").click() },
};
$$("[data-browse]").forEach(b => (b.onclick = async () => {
  const cfg = BROWSE[b.dataset.browse];
  const start = $(cfg.input).value || S.st?.settings.folder || "";
  const p = await browse({ ...cfg, path: start });
  if (p) { $(cfg.input).value = p; cfg.after(); }
}));

// ----------------------------------------------------------------------------- boot

(async function init() {
  await run("Connecting", async () => {
    await refreshState();
    const folder = S.st.settings.folder;
    if (folder) {
      try { await openFolder(folder); } catch (e) { toast(`Could not reopen ${folder}: ${e.message}`); }
    }
  });
  switchTab("image");
})();

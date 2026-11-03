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
  renderPickers(st);
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
  applyBeamstop(st);
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
  if (S.tab === "curves" || S.tab === "results") switchTab("image");
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
  applyState(r.state);
  S.sel.clear(); S.current = null;
  setFiles(r.files);
  if (S.trackTimer) startTracking();
  if (r.auto_loaded.length) toast(`Also loaded ${r.auto_loaded.join(" and ")} found in this folder`, "info");
  showView();
}

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

// ----------------------------------------------------------------------------- folder / PONI / mask pickers

const baseName = p => (p || "").split(/[\\/]/).filter(Boolean).pop() || p;

const PICKERS = {
  folder: { empty: "No folder open", load: p => run("Opening folder", () => openFolder(p)) },
  poni: {
    empty: "No PONI loaded", load: p => run("Loading PONI", async () => {
      applyState(await api("/api/poni", { path: p }));
      toast(`Loaded ${baseName(p)}`, "info");
      showView();
    }),
  },
  mask: {
    empty: "None – gaps are still masked", clearable: true, load: p => run(p ? "Loading mask" : "Removing mask", async () => {
      applyState(await api("/api/mask", { path: p }));
      toast(p ? `Loaded ${baseName(p)}` : "Mask removed", "info");
      showView();
    }),
  },
};

function renderPickers(st) {
  for (const [kind, cfg] of Object.entries(PICKERS)) {
    const box = $(`.picker[data-kind=${kind}]`), path = st.settings[kind];
    const name = $(".picker-name", box);
    name.textContent = path ? baseName(path) : cfg.empty;
    name.classList.toggle("empty", !path);
    $(".picker-path", box).textContent = path || "";
    $(".picker-text", box).title = path || "";
  }
  $("#folder-label").textContent = st.settings.folder || "No folder";
}

/** Ask for a path with the operating system's own dialog (falls back to the in-page browser). */
async function pickPath(kind) {
  const s = S.st?.settings || {};
  const initial = (kind === "folder" ? s.folder : s[kind]) || s.folder || "";
  status("A file dialog is open (it may be behind this window)…", true);
  try {
    const r = await api("/api/pick", { kind, initial });
    return r.paths ?? r.path;
  } catch (e) {
    if (!/Native dialogs unavailable/.test(e.message)) throw e;
    const fallback = {
      folder: { title: "Choose image folder", dirMode: true }, poni: { title: "Choose PONI file", exts: ".poni" },
      mask: { title: "Choose mask file", exts: ".msk,.edf,.tif,.tiff" }, nxs: { title: "Choose NeXus file", exts: ".nxs,.h5,.hdf5" },
      dat: { title: "Load .dat curves", exts: ".dat", multi: true },
    }[kind];
    status("");
    return await browse({ ...fallback, path: initial });
  } finally {
    status("");
  }
}

function closePickerMenus(except) {
  $$(".picker-menu").forEach(m => { if (m !== except) m.hidden = true; });
}

function openPickerMenu(box) {
  const kind = box.dataset.kind, cfg = PICKERS[kind], menu = $(".picker-menu", box);
  if (!menu.hidden) return (menu.hidden = true);
  closePickerMenus(menu);
  const recent = (S.st?.settings.recent?.[kind] || []).filter(p => p !== S.st.settings[kind]);
  menu.innerHTML = `<input class="picker-paste" placeholder="Paste a path and press Enter" spellcheck="false">` +
    (recent.length ? `<h4>Recent</h4>` + recent.map(p => `<button type="button" class="picker-item" data-path="${esc(p)}"><b>${esc(baseName(p))}</b><span>${esc(p)}</span></button>`).join("")
      : `<p class="muted small" style="margin:4px 6px">Nothing recent yet.</p>`) +
    (cfg.clearable && S.st?.settings[kind] ? `<button type="button" class="picker-item danger" data-path="">Remove mask</button>` : "");
  menu.hidden = false;
  const input = $(".picker-paste", menu);
  input.focus();
  input.onkeydown = e => {
    if (e.key === "Enter" && input.value.trim()) { menu.hidden = true; cfg.load(input.value.trim().replace(/^"|"$/g, "")); }
    if (e.key === "Escape") menu.hidden = true;
  };
  menu.onclick = e => {
    const item = e.target.closest(".picker-item");
    if (!item) return;
    menu.hidden = true;
    cfg.load(item.dataset.path);
  };
}

$$(".picker").forEach(box => {
  const kind = box.dataset.kind;
  $(".picker-browse", box).onclick = async () => {
    closePickerMenus();
    try {
      const p = await pickPath(kind);
      if (p) PICKERS[kind].load(p);
    } catch (e) { toast(e.message); }
  };
  $(".picker-more", box).onclick = () => openPickerMenu(box);
});
document.addEventListener("click", e => { if (!e.target.closest(".picker")) closePickerMenus(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closePickerMenus(); });

$("#nxs-load").onclick = async () => {
  let path;
  try { path = await pickPath("nxs"); } catch (e) { return toast(e.message); }
  if (!path) return;
  run("Converting NeXus file (may take a while)", async () => {
    const r = await api("/api/nxs", { path });
    applyState(r);
    S.sel.clear(); S.current = null;
    setFiles(r.files);
    if (r.warning) toast(r.warning); else toast("NeXus file converted and loaded", "info");
    showView();
  });
};
// ----------------------------------------------------------------------------- auto beamstop mask

const BS_KEYS = ["threshold", "smooth", "margin", "min_area"];
const BS_DEFAULTS = { threshold: 0.3, smooth: 2, margin: 3, min_area: 200, bridge_gaps: true };

function applyBeamstop(st) {
  const cfg = st.settings.beamstop || {};
  $("#bs-enable").checked = !!cfg.enabled;
  $("#bs-controls").classList.toggle("hidden", !cfg.enabled);
  for (const k of BS_KEYS) {
    const el = $("#bs-" + k);
    if (document.activeElement !== el) el.value = cfg[k];  // don't fight a slider being dragged
    $(`#bs-${k}-v`).textContent = el.value;
  }
  $("#bs-bridge").checked = !!cfg.bridge_gaps;
  const info = $("#bs-info"), s = st.beamstop_stats;
  info.classList.remove("warn");
  if (!cfg.enabled) info.textContent = "";
  else if (!s) info.textContent = "Not detected yet – select an image and press “Detect on this image”.";
  else if (!s.disk_radius_px) {
    info.textContent = `No beamstop disk found on ${s.ref}: only ${s.pixels} px at the center are masked. Try a higher threshold, or check the beam center.`;
    info.classList.add("warn");
  } else {
    info.textContent = `Detected on ${s.ref}: disk radius ≈ ${s.disk_radius_px} px, ${s.pixels.toLocaleString()} px masked` +
      (s.arm_pixels ? ` (${s.arm_pixels.toLocaleString()} in the arm / beyond the disk).` : " (no arm found).");
  }
}

let bsSeq = 0;
/** Send beamstop parameters, re-detect on the shown image and redraw. */
async function updateBeamstop(patch) {
  const seq = ++bsSeq;
  const body = { ...patch };
  if (S.current) body.id = S.current;
  await run("Detecting beamstop", async () => {
    const st = await api("/api/beamstop", body);
    if (seq !== bsSeq) return;  // a newer slider position is already on its way
    applyState(st);
    if (S.tab !== "curves" && S.current) await render2D();
  });
}

$("#bs-enable").onchange = e => {
  if (e.target.checked && !S.current) {
    e.target.checked = false;
    return toast("Select an image (ideally one with good statistics) to detect the beamstop on.");
  }
  if (e.target.checked && S.tab !== "image" && S.tab !== "anis") switchTab("image");
  updateBeamstop({ enabled: e.target.checked });
};
let bsTimer;
for (const k of BS_KEYS) {
  const el = $("#bs-" + k);
  el.oninput = () => {
    $(`#bs-${k}-v`).textContent = el.value;
    clearTimeout(bsTimer);
    bsTimer = setTimeout(() => updateBeamstop({ [k]: el.value }), 250);
  };
}
$("#bs-bridge").onchange = e => updateBeamstop({ bridge_gaps: e.target.checked });
$("#bs-detect").onclick = () => S.current ? updateBeamstop({}) : toast("Select an image first.");
$("#bs-reset").onclick = () => updateBeamstop({ ...BS_DEFAULTS });
$("#bs-save").onclick = async () => {
  const v = await dialog("Save beamstop mask", `<p class="muted">Beamstop + loaded mask, as a pyFAI-compatible .msk (EDF) file in the image folder.
    Gaps are always masked automatically, so they don't need to be in it.</p>
    <label class="field">File name<input name="name" value="beamstop_mask.msk" required></label>`);
  if (!v) return;
  run("Saving mask", async () => toast(`Saved ${(await api("/api/beamstop/save", v)).path}`, "info"));
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
  $("#toolbar-2d").classList.toggle("hidden", tab === "curves" || tab === "results");
  $("#toolbar-1d").classList.toggle("hidden", tab !== "curves");
  $("#toolbar-results").classList.toggle("hidden", tab !== "results");
  $("#results-wrap").classList.toggle("hidden", tab !== "results");
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
  if (S.tab === "results") return showResults();
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
    xaxis: { title: { text: isCake ? unitLabel() : "x (px)" }, showgrid: false, zeroline: false, constrain: "domain" },
    yaxis: isCake ? { title: { text: "χ (°)" }, showgrid: false, zeroline: false, autorange: true, scaleanchor: false }
      : { title: { text: "y (px)" }, showgrid: false, zeroline: false, autorange: true, scaleanchor: "x", constrain: "domain" },
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
      xaxis: { title: { text: r.label }, type: $("#logx").checked ? "log" : "linear", gridcolor: cssVar("--border"), zeroline: false },
      yaxis: { title: { text: "Intensity" }, type: $("#logy").checked ? "log" : "linear", gridcolor: cssVar("--border"), exponentformat: "power", zeroline: false },
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
  let files;
  try { files = await pickPath("dat"); } catch (e) { return toast(e.message); }
  if (!files?.length) return;
  run("Loading curves", async () => {
    const r = await api("/api/curves/load_dat", { files });
    S.st.curves = r.curves; renderCurveList(r.curves);
    files.forEach(f => S.curveSel.add(baseName(f).replace(/\.dat$/i, "")));
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
    xaxis: { title: { text: "χ (°)" }, range: [-180, 180], dtick: 45, gridcolor: cssVar("--border"), zeroline: false },
    yaxis: { title: { text: "I" }, gridcolor: cssVar("--border"), zeroline: false },
    showlegend: false,
    shapes: [{ type: "line", x0: m.orientation_deg, x1: m.orientation_deg, yref: "paper", y0: 0, y1: 1, line: { color: "#d62728", dash: "dot", width: 1 } },
      { type: "line", x0: m.orientation_deg - 180 * Math.sign(m.orientation_deg || 1), x1: m.orientation_deg - 180 * Math.sign(m.orientation_deg || 1), yref: "paper", y0: 0, y1: 1, line: { color: "#d62728", dash: "dot", width: 1 } }],
  });
  layout.showlegend = true;
  layout.legend = { orientation: "h", x: 1, xanchor: "right", y: 1, yanchor: "bottom", font: { size: 11 } };
  Plotly.react(plot, [
    { type: "scatter", mode: "lines+markers", x: r.chi, y: r.intensity, marker: { size: 3 }, line: { width: 1.2, color: PALETTE[0] },
      name: `I(χ), q ${fmt(s.q_min * f)}–${fmt(s.q_max * f)}` },
    { type: "scatter", mode: "lines", x: r.chi, y: r.fit, line: { width: 1.5, color: "#d62728", dash: "dash" }, name: "fit I₀[1 + A cos 2(χ − φ)]" },
  ], layout, { responsive: true, displaylogo: false });
}

// ----------------------------------------------------------------------------- saving anisotropy results

function anisParams() {
  return { solid_angle: $("#anis-sa").checked, polarization: $("#anis-pol").value };
}

/** Analyse images one by one (progress bar), saving profiles + result rows on the server. */
async function analyzeAnisotropy(ids) {
  const s = S.st.settings;
  if (s.q_min == null || s.q_max == null) return toast("Set q min and q max first (e.g. around the peak).");
  if (!ids.length) return toast("Select images first.");
  const prog = $("#progress");
  prog.hidden = false; prog.max = ids.length; prog.value = 0;
  let done = 0;
  try {
    for (const id of ids) {
      status(`Anisotropy ${id} (${done + 1}/${ids.length})…`, true);
      try {
        await api("/api/anisotropy/analyze", { id, ...anisParams() });
        done++;
      } catch (e) {
        toast(`${id}: ${e.message}`);
        if (/PONI|folder|q min/.test(e.message)) break;
      }
      prog.value = done;
    }
  } finally {
    prog.hidden = true;
    status("");
  }
  if (!done) return;
  toast(`Saved ${done} result(s) to analysis/anisotropy_results.csv`, "info");
  if (ids.length > 1) { S.results.kind = "anisotropy"; switchTab("results"); }
}
$("#anis-save").onclick = () => S.current ? analyzeAnisotropy([S.current]) : toast("Select an image first.");
$("#anis-run-sel").onclick = () => analyzeAnisotropy(leafIds().filter(id => S.sel.has(id)));
$("#anis-run-all").onclick = () => analyzeAnisotropy(leafIds());
$("#anis-save-map").onclick = () => run("Saving map", async () => {
  if (!S.current) throw new Error("Select an image first.");
  const r = await api("/api/anisotropy/save_map", { id: S.current, mode: $("#anis-mode").value, ...anisParams() });
  toast(`Saved ${r.path}`, "info");
});

// ----------------------------------------------------------------------------- results tab

S.results = { tables: [], kind: "anisotropy", sel: new Set(), axes: {}, sortKey: null, sortDir: 1 };
const RES_HIDDEN = new Set(["id", "profile"]);

const resTable = () => S.results.tables.find(t => t.kind === S.results.kind) || S.results.tables[0];

async function showResults() {
  await run("Loading results", async () => {
    S.results.tables = (await api("/api/results")).tables;
    const kindSel = $("#res-kind");
    kindSel.innerHTML = S.results.tables.map(t => `<option value="${t.kind}">${esc(t.title)} (${t.rows.length})</option>`).join("");
    const t = resTable();
    if (!t) return;
    S.results.kind = kindSel.value = t.kind;
    const ids = new Set(t.rows.map(r => r.id));
    for (const id of [...S.results.sel]) if (!ids.has(id)) S.results.sel.delete(id);
    // axis choices: numeric columns + acquisition time
    const plottable = t.columns.filter(c => c.numeric || c.key === "time");
    const ax = S.results.axes[t.kind] ||= { x: "index", y: plottable.find(c => c.key === "amplitude")?.key || plottable[0]?.key };
    for (const [sel, key] of [["#res-x", "x"], ["#res-y", "y"]]) {
      $(sel).innerHTML = plottable.map(c => `<option value="${c.key}">${esc(c.label)}</option>`).join("");
      $(sel).value = ax[key];
    }
    $("#res-info").textContent = t.rows.length
      ? `${t.rows.length} row(s) · saved in ${t.file} · click rows (Ctrl/Shift for several) to highlight them or plot their profiles`
      : "";
    renderResultsTable(t);
    await plotResults(t);
  });
}

function sortedRows(t) {
  const { sortKey, sortDir } = S.results;
  if (!sortKey) return t.rows;
  return [...t.rows].sort((a, b) => {
    const x = a[sortKey], y = b[sortKey];
    if (x == null) return 1;
    if (y == null) return -1;
    return (x > y ? 1 : x < y ? -1 : 0) * sortDir;
  });
}

function renderResultsTable(t) {
  const cols = t.columns.filter(c => !RES_HIDDEN.has(c.key));
  const cell = (c, v) => v == null ? "" : c.numeric && typeof v === "number" ? fmt(v, 4) : esc(v);
  const arrow = c => S.results.sortKey === c.key ? (S.results.sortDir > 0 ? " ▲" : " ▼") : "";
  $("#res-table").innerHTML = `<thead><tr>${cols.map(c => `<th data-key="${c.key}">${esc(c.label)}${arrow(c)}</th>`).join("")}</tr></thead>
    <tbody>${sortedRows(t).map(r => `<tr data-id="${esc(r.id)}" class="${S.results.sel.has(r.id) ? "selected" : ""}">${cols.map(c => `<td>${cell(c, r[c.key])}</td>`).join("")}</tr>`).join("")}</tbody>`;
}

$("#res-table").addEventListener("click", e => {
  const th = e.target.closest("th");
  if (th) {
    S.results.sortDir = S.results.sortKey === th.dataset.key ? -S.results.sortDir : 1;
    S.results.sortKey = th.dataset.key;
    return renderResultsTable(resTable());
  }
  const tr = e.target.closest("tr[data-id]");
  if (tr) toggleResultRow(tr.dataset.id, e);
});

function toggleResultRow(id, e) {
  const sel = S.results.sel;
  if (e?.shiftKey && S.results.anchor) {
    const ids = sortedRows(resTable()).map(r => r.id);
    const [a, b] = [ids.indexOf(S.results.anchor), ids.indexOf(id)].sort((x, y) => x - y);
    ids.slice(a, b + 1).forEach(i => sel.add(i));
  } else if (e?.ctrlKey || e?.metaKey) {
    sel.has(id) ? sel.delete(id) : sel.add(id);
  } else {
    const only = sel.size === 1 && sel.has(id);
    sel.clear();
    if (!only) sel.add(id);
  }
  S.results.anchor = id;
  const t = resTable();
  renderResultsTable(t);
  plotResults(t);
}

async function plotResults(t) {
  const mode = $("#res-mode").value;
  $$(".res-xy").forEach(el => el.classList.toggle("hidden", mode !== "scatter"));
  if (!t.rows.length) {
    return showPlaceholder(true, t.kind === "anisotropy"
      ? "No saved results yet. On the Anisotropy tab, set q min / q max and press “Save to results” or “Run on all”."
      : "No saved results yet.");
  }
  showPlaceholder(false);
  const label = key => t.columns.find(c => c.key === key)?.label || key;
  const layout = Object.assign(baseLayout(), {
    hovermode: "closest", dragmode: "zoom", uirevision: `res:${t.kind}:${mode}`,
    legend: { orientation: "h", y: 1.02, yanchor: "bottom" },
  });
  layout.margin.t = 40;
  let traces;
  if (mode === "scatter") {
    const { x, y } = S.results.axes[t.kind];
    // one series per parameter set (e.g. q range), sorted along x
    const groups = new Map();
    for (const r of t.rows) {
      const g = t.kind === "anisotropy" ? `q ${fmt(r.q_min_A)}–${fmt(r.q_max_A)} Å⁻¹` : "results";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(r);
    }
    traces = [...groups].map(([g, rows], i) => {
      rows = rows.filter(r => r[x] != null && r[y] != null).sort((a, b) => (a[x] > b[x] ? 1 : -1));
      return {
        type: "scatter", mode: "lines+markers", name: g, x: rows.map(r => r[x]), y: rows.map(r => r[y]),
        text: rows.map(r => r.image), customdata: rows.map(r => r.id),
        marker: { size: rows.map(r => S.results.sel.has(r.id) ? 12 : 7), color: PALETTE[i % PALETTE.length],
          line: { width: rows.map(r => S.results.sel.has(r.id) ? 2 : 0), color: cssVar("--text") } },
        line: { width: 1, color: PALETTE[i % PALETTE.length] },
        hovertemplate: "%{text}<br>%{x}<br>%{y}<extra></extra>",
      };
    });
    layout.showlegend = traces.length > 1;
    layout.xaxis = { title: { text: label(x) }, gridcolor: cssVar("--border"), zeroline: false };
    layout.yaxis = { title: { text: label(y) }, gridcolor: cssVar("--border"), zeroline: false };
  } else {
    let ids = [...S.results.sel];
    if (!ids.length) ids = t.rows.slice(0, 20).map(r => r.id);
    const r = await api("/api/results/profiles", { kind: t.kind, ids });
    traces = r.profiles.flatMap((p, i) => {
      const [xc, yc, fitc] = p.columns;
      const color = PALETTE[i % PALETTE.length];
      const out = [{ type: "scatter", mode: "lines", name: p.label, x: p.data[xc], y: p.data[yc], line: { width: 1.3, color }, legendgroup: p.id }];
      if (fitc) out.push({ type: "scatter", mode: "lines", name: `${p.label} fit`, x: p.data[xc], y: p.data[fitc], showlegend: false,
        legendgroup: p.id, line: { width: 1, color, dash: "dash" }, hoverinfo: "skip" });
      return out;
    });
    const xc = r.profiles[0]?.columns[0];
    layout.xaxis = { title: { text: xc === "chi_deg" ? "χ (°)" : xc }, gridcolor: cssVar("--border"), zeroline: false, ...(xc === "chi_deg" ? { range: [-180, 180], dtick: 45 } : {}) };
    layout.yaxis = { title: { text: "Intensity" }, gridcolor: cssVar("--border"), zeroline: false };
    layout.showlegend = true;
  }
  Plotly.react($("#plot"), traces, layout, PLOT_CONFIG);
  bindPlotEvents();
}

$("#res-kind").onchange = e => { S.results.kind = e.target.value; S.results.sel.clear(); showResults(); };
$("#res-mode").onchange = () => plotResults(resTable());
for (const [sel, key] of [["#res-x", "x"], ["#res-y", "y"]]) {
  $(sel).onchange = e => { S.results.axes[S.results.kind][key] = e.target.value; plotResults(resTable()); };
}
$("#res-delete").onclick = () => run("Deleting", async () => {
  const ids = [...S.results.sel];
  if (!ids.length) throw new Error("Select rows to delete.");
  if (!confirm(`Delete ${ids.length} row(s) from the results file? (Saved profile files are kept.)`)) return;
  await api("/api/results/delete", { kind: S.results.kind, ids });
  S.results.sel.clear();
  await showResults();
});
$("#res-open").onclick = () => run("Opening folder", () => api("/api/open_folder", {}));

// ----------------------------------------------------------------------------- saving figures

/** Copy of a figure's layout with a white background and dark text (for papers / slides). */
function lightLayout(layout) {
  const l = JSON.parse(JSON.stringify(layout));
  Object.assign(l, { paper_bgcolor: "#ffffff", plot_bgcolor: "#ffffff" });
  l.font = { ...(l.font || {}), color: "#111111" };
  for (const k of Object.keys(l)) {
    if (/^[xy]axis\d*$/.test(k)) Object.assign(l[k], { gridcolor: "#dddddd", linecolor: "#444444", zerolinecolor: "#bbbbbb" });
  }
  return l;
}

function defaultFigureName(target) {
  const img = S.current ? S.current.replace(/\.[^.]+$/, "").replace(/#/g, "_f") : "figure";
  if (target === "chi-plot") return `${img}_chi_profile`;
  return { image: img, cake: `${img}_cake`, anis: `${img}_anisotropy_${$("#anis-mode").value}`,
    curves: "curves", results: `${S.results.kind}_results` }[S.tab] || img;
}

$$(".save-fig").forEach(btn => (btn.onclick = async () => {
  const el = $("#" + btn.dataset.target);
  if (!el.data?.length) return toast("Nothing plotted yet.");
  // export size independent of the window: what is on screen, but never smaller than a usable figure
  const wide = btn.dataset.target === "chi-plot";
  const w0 = Math.max(Math.round(el._fullLayout?.width || 0), wide ? 1000 : 900);
  const h0 = Math.max(Math.round(el._fullLayout?.height || 0), wide ? 450 : 600);
  const v = await dialog("Save figure", `<p class="muted">Saved as PNG (3× resolution) and SVG (vector, editable in Inkscape / Illustrator) in analysis/figures/.</p>
    <label class="field">File name<input name="name" value="${esc(defaultFigureName(btn.dataset.target))}" required></label>
    <div class="grid2"><label class="field">Width (px)<input name="width" type="number" min="200" max="4000" value="${w0}" required></label>
      <label class="field">Height (px)<input name="height" type="number" min="150" max="4000" value="${h0}" required></label></div>
    <label><input type="checkbox" name="light" checked> White background (for papers / slides)</label>`);
  if (!v) return;
  run("Saving figure", async () => {
    const layout = v.light ? lightLayout(el.layout) : JSON.parse(JSON.stringify(el.layout));
    delete layout.width; delete layout.height;
    const fig = { data: el.data, layout };
    const size = { width: +v.width, height: +v.height };
    const png = await Plotly.toImage(fig, { format: "png", scale: 3, ...size });
    const svg = await Plotly.toImage(fig, { format: "svg", ...size });
    const r = await api("/api/save_figure", { name: v.name, png, svg });
    toast(`Saved ${r.paths.map(baseName).join(" and ")} in analysis/figures/`, "info");
  });
}));

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
  if (S.tab === "results" && ev.points?.[0]?.customdata) return toggleResultRow(ev.points[0].customdata, ev.event);
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
  if (S.tab === "results") {
    ro.innerHTML = `${esc(pt.text || pt.data.name || "")} · x <b>${typeof pt.x === "number" ? fmt(pt.x) : esc(pt.x)}</b> · y <b>${fmt(pt.y)}</b>`;
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
      if (p.masked) html += ` · <b>${p.masked === "beamstop" ? "beamstop (auto)" : "masked"}</b>`;
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
/** Call done(ok) once when the modal is confirmed or dismissed. Driven by the button
 *  submit / Esc events themselves: the dialog "close" event is not delivered reliably
 *  in every browser (e.g. embedded webviews). */
function onModalDone(done) {
  const dlg = $("#modal"), form = $("#modal-form");
  let finished = false;
  const finish = ok => {
    if (finished) return;
    finished = true;
    form.removeEventListener("submit", onSubmit);
    dlg.removeEventListener("cancel", onCancel);
    dlg.removeEventListener("close", onClose);
    done(ok);
  };
  const onSubmit = e => finish(e.submitter?.value === "ok");  // fires before the dialog closes
  const onCancel = () => finish(false);                        // Esc
  const onClose = () => finish(dlg.returnValue === "ok");      // fallback
  form.addEventListener("submit", onSubmit);
  dlg.addEventListener("cancel", onCancel);
  dlg.addEventListener("close", onClose);
}

function dialog(title, html, prepare) {
  const dlg = $("#modal");
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = html;
  const form = $("#modal-form");
  prepare?.(form);
  dlg.showModal();
  form.querySelector("input:not([type=hidden]), select")?.focus();
  return new Promise(resolve => {
    onModalDone(ok => resolve(ok ? Object.fromEntries(new FormData(form)) : null));
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
      if (li && !multi) { chosen = new Set([li.dataset.f]); $("#modal-ok").click(); }
    };
    onModalDone(ok => {
      if (!ok || !cur) return resolve(null);
      if (dirMode) return resolve(cur.path);
      if (!chosen.size) return resolve(null);
      resolve(multi ? [...chosen].map(f => join(cur.path, f)) : join(cur.path, [...chosen][0]));
    });
    dlg.showModal();
    go(path);
  });
}

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

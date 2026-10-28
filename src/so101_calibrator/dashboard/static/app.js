// so101-calibrator dashboard: polls /api/state and renders it. No framework, no build step.
"use strict";

const $ = (id) => document.getElementById(id);
const JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"];
const MAX = 4095;
const ICON = { pass: "✓", warn: "!", fail: "✕", skipped: "–", running: "•", ready: "›", pending: "" };
const FLOW_TITLES = { calibrate: "Calibration", verify: "Verification", teleop: "Teleop test" };

let state = null;
let flow = "calibrate";
let viewStep = null; // step id the user clicked in the list (null: follow the current step)
let selectedArm = null;
let diffSel = { a: null, b: null };
let lastArmsKey = "";
let busy = false;

// Write innerHTML only when it changed: re-rendering every poll would swallow clicks mid-press.
function setHTML(el, html) {
  if (typeof el === "string") el = $(el);
  if (el._html !== html) { el.innerHTML = html; el._html = html; }
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opts);
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

// ---------------------------------------------------------------- start form
function portOptions(selected, allowDiscover = true) {
  const ports = state ? state.ports : {};
  let html = allowDiscover ? `<option value="">Find by unplugging…</option>` : "";
  for (const [p, serial] of Object.entries(ports)) {
    html += `<option value="${esc(p)}" ${p === selected ? "selected" : ""}>${esc(p)}${serial ? " · " + esc(serial) : ""}</option>`;
  }
  return html;
}

function renderForm() {
  const f = $("start-form");
  const roleSeg = (name) => `
    <label>Arm<div class="seg" data-name="${name}">
      <button type="button" data-v="follower" class="on">Follower</button>
      <button type="button" data-v="leader">Leader</button></div></label>`;
  if (flow === "calibrate") {
    f.innerHTML = `${roleSeg("role")}
      <label>LeRobot id <input type="text" name="arm_id" placeholder="follower"></label>
      <label>Port <select name="port">${portOptions("")}</select></label>
      <label>Note <input type="text" name="note" placeholder="e.g. after replacing elbow motor"></label>
      <label data-only="follower">Supply <select name="supply"><option value="">12 V (default)</option><option value="7.4v">7.4 V motors</option></select></label>
      <label class="check"><input type="checkbox" name="export" checked> Export to LeRobot when saved</label>
      <button class="primary" type="submit">Start calibration</button>`;
  } else if (flow === "verify") {
    f.innerHTML = `${roleSeg("role")}
      <label>LeRobot id <input type="text" name="arm_id" placeholder="follower"></label>
      <label>Port <select name="port">${portOptions("")}</select></label>
      <button class="primary" type="submit">Start verification</button>`;
  } else {
    f.innerHTML = `
      <div class="group-title">Leader</div>
      <div class="pair"><label>Id <input type="text" name="leader_arm_id" placeholder="leader"></label>
      <label>Port <select name="leader_port">${portOptions("")}</select></label></div>
      <div class="group-title">Follower</div>
      <div class="pair"><label>Id <input type="text" name="follower_arm_id" placeholder="follower"></label>
      <label>Port <select name="follower_port">${portOptions("")}</select></label></div>
      <label>Duration (s) <input type="number" name="seconds" value="15" min="3" max="120"></label>
      <button class="primary" type="submit">Start teleop test</button>`;
  }
  f.querySelectorAll(".seg").forEach((seg) => {
    seg.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      seg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      const role = b.dataset.v;
      f.querySelectorAll("[data-only]").forEach((el) => (el.hidden = el.dataset.only !== role));
      const id = f.querySelector("[name=arm_id]");
      if (id) id.placeholder = role;
    });
  });
}

$("flow-seg").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  flow = b.dataset.flow;
  $("flow-seg").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
  renderForm();
});

$("start-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = { flow };
  for (const el of f.elements) {
    if (!el.name) continue;
    body[el.name] = el.type === "checkbox" ? el.checked : el.value;
  }
  const seg = f.querySelector(".seg[data-name=role] .on");
  if (seg) body.role = seg.dataset.v;
  if (body.arm_id === "") delete body.arm_id;
  try {
    await api("/api/session", body);
    viewStep = null;
    refresh();
  } catch (err) {
    toast(err.message);
  }
});

$("close-btn").addEventListener("click", () => act("close"));

async function act(action) {
  if (busy) return;
  busy = true;
  try {
    await api("/api/session/action", { action });
    viewStep = null;
  } catch (err) {
    toast(err.message);
  } finally {
    busy = false;
    refresh();
  }
}

// ---------------------------------------------------------------- session view
function renderSession(s) {
  const active = s && !s.finished;
  $("start-panel").hidden = active;
  $("steps-panel").hidden = !s;
  $("empty-state").hidden = !!s;
  if (!s) {
    $("step-body").hidden = true;
    $("result-body").hidden = true;
    setHTML("log", "");
    return;
  }
  $("flow-title").textContent = FLOW_TITLES[s.flow];
  const cur = s.current ? s.current.id : null;
  setHTML("steps", s.steps.map((st, i) => `
    <li class="${st.status} ${st.id === (viewStep || cur) ? "current" : ""}" data-id="${esc(st.id)}">
      <span class="ic">${ICON[st.status] || i + 1}</span><span>${esc(st.label)}</span></li>`).join(""));

  const shown = viewStep ? s.steps.find((x) => x.id === viewStep) : null;
  if (s.current && !shown) {
    renderCurrent(s, s.current);
  } else if (shown) {
    renderPast(s, shown);
  } else {
    $("step-body").hidden = true;
  }
  renderResult(s);
  setHTML("log", s.events.slice().reverse().map((e) =>
    `<li class="${esc(e.level)}"><span class="t">${e.t.toFixed(1)}s</span>${esc(e.msg)}</li>`).join(""));
}

$("steps").addEventListener("click", (e) => {
  const li = e.target.closest("li");
  if (!li || !state || !state.session) return;
  const cur = state.session.current;
  viewStep = cur && li.dataset.id === cur.id ? null : li.dataset.id;
  renderSession(state.session);
});

function checksHtml(checks) {
  return checks.map((c) => `<li class="${c.status}"><div>${esc(c.message)}</div>${c.fix && c.status !== "pass" ? `<div class="fix">${esc(c.fix)}</div>` : ""}</li>`).join("");
}

function renderCurrent(s, c) {
  $("step-body").hidden = false;
  const idx = s.steps.findIndex((x) => x.id === c.id);
  $("step-count").textContent = `Step ${idx + 1} of ${s.steps.length}${c.live ? " · live" : ""}`;
  $("step-title").textContent = c.label;
  const chip = $("step-status");
  chip.className = `chip ${c.status}`;
  chip.textContent = c.status === "running" ? "recording" : c.status;
  $("step-instructions").textContent = c.instructions;
  setHTML("step-progress", progressHtml(c));
  const btn = (action, label, cls, show) => (show ? `<button class="${cls}" data-act="${action}">${label}</button>` : "");
  setHTML("step-actions",
    btn("start", c.live ? "Start" : "Run step", "primary", c.can.start) +
    btn("stop", "Stop & check", "primary", c.can.stop) +
    btn("next", "Continue anyway", "primary", c.can.next) +
    btn("retry", "Retry", c.can.next ? "secondary" : "primary", c.can.retry && c.status !== "running") +
    btn("retry", "Cancel", "ghost", c.status === "running") +
    btn("force", "Accept failure", "danger", c.can.force) +
    btn("skip", "Skip", "ghost", c.can.skip) +
    btn("abort", "Abort run", "ghost", true));
  const step = s.steps[idx];
  setHTML("step-checks", checksHtml(step.checks));
}

function renderPast(s, st) {
  $("step-body").hidden = false;
  const idx = s.steps.findIndex((x) => x.id === st.id);
  $("step-count").textContent = `Step ${idx + 1} of ${s.steps.length} · viewing`;
  $("step-title").textContent = st.label;
  $("step-status").className = `chip ${st.status}`;
  $("step-status").textContent = st.status;
  $("step-instructions").textContent = "";
  setHTML("step-progress", "");
  setHTML("step-actions", `<button class="secondary" data-act="back">Back to current step</button>`);
  setHTML("step-checks", st.checks.length ? checksHtml(st.checks) : `<li>Not run yet.</li>`);
}

function renderResult(s) {
  const box = $("result-body");
  if (!s.finished) { box.hidden = true; return; }
  box.hidden = false;
  if (!viewStep) $("step-body").hidden = true;
  const r = s.result;
  const text = { pass: "All checks passed", warn: "Done, with warnings", fail: "Failed", aborted: "Aborted" }[r];
  const rec = s.record ? `<p>Saved as <b>${esc(Object.values(s.targets)[0].arm_id)} v${s.record}</b>. See the history below to compare, export or roll back.</p>` : "";
  const bad = s.steps.flatMap((st) => st.checks.filter((c) => c.status !== "pass"));
  setHTML(box, `<div class="result ${r}"><div class="eyebrow">${esc(FLOW_TITLES[s.flow])} finished</div>
    <div class="big">${text}</div>${rec}
    ${bad.length ? `<ul class="checks">${checksHtml(bad)}</ul>` : ""}
    <div class="actions"><button class="primary" data-act="close">New run</button></div></div>`);
}

function progressHtml(c) {
  const p = c.progress || {};
  if (p.phase) {
    const msg = { idle: "Press Start, then unplug the USB cable.", unplug: "Waiting for you to <b>unplug</b> the USB cable…",
      replug: `Found <b>${esc(p.removed.join(", "))}</b>. Now <b>plug it back in</b>…`, back: "Plugged back in.",
      ambiguous: "More than one port disappeared." }[p.phase];
    return `<div class="callout">${msg}</div>`;
  }
  if (p.joint !== undefined) {
    const done = Object.keys(p.signs || {}).length;
    if (!p.joint) return `<div class="callout">All joints recorded.</div>`;
    return `<div class="callout">Now move <b>${esc(p.joint)}</b>: ${esc(p.prompt)}
      <div class="meter" style="margin-top:8px"><i style="width:${(done / 6) * 100}%"></i></div></div>`;
  }
  if (p.elapsed !== undefined) {
    return `<div class="callout">Teleoperating · ${p.elapsed.toFixed(0)} / ${p.seconds.toFixed(0)} s · ${p.samples} cycles
      <div class="meter" style="margin-top:8px"><i style="width:${Math.min(100, (p.elapsed / p.seconds) * 100)}%"></i></div></div>`;
  }
  if (p.recorded) {
    const fills = Object.values(p.recorded).map((r) => r.fill);
    const avg = fills.reduce((a, b) => a + b, 0) / fills.length;
    return `<div class="callout">Coverage vs. a typical SO-101 sweep: <b>${Math.round(avg * 100)}%</b>
      <div class="meter" style="margin-top:8px"><i style="width:${avg * 100}%"></i></div></div>`;
  }
  return "";
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  if (b.dataset.act === "back") { viewStep = null; renderSession(state.session); return; }
  act(b.dataset.act);
});

// ---------------------------------------------------------------- live joints
function pct(v) { return `${(Math.max(0, Math.min(MAX, v)) / MAX) * 100}%`; }

function renderLive(s) {
  const box = $("live");
  if (!s) { setHTML(box, `<p class="muted">Joint positions appear once the motors are connected.</p>`); return; }
  const prog = (s.current && s.current.progress) || {};
  const rec = prog.recorded || {};
  const hot = prog.joint;
  let html = "";
  for (const [role, arm] of Object.entries(s.arms)) {
    html += `<div class="arm-title"><span>${esc(role)} · ${esc(arm.arm_id)}</span><span>${arm.connected ? esc(arm.port) : "not connected"}</span></div>`;
    for (const j of JOINTS) {
      const d = arm.joints[j];
      const r = rec[j];
      const cal = d.cal_min !== null ? `<i class="cal" style="left:${pct(d.cal_min)};width:calc(${pct(d.cal_max)} - ${pct(d.cal_min)})"></i>` : "";
      const recBar = r ? `<i class="rec" style="left:${pct(r.min)};width:calc(${pct(r.max)} - ${pct(r.min)})"></i>` : "";
      const pos = d.raw !== null && arm.connected ? `<i class="pos" style="left:${pct(d.raw)}"></i>` : "";
      html += `<div class="joint ${hot === j ? "hot" : ""}"><span class="name" title="${j}">${j}</span>
        <div class="track">${cal}${recBar}<i class="mid"></i>${pos}</div>
        <span class="num">${d.raw ?? "–"}</span><span class="num norm">${d.norm === null ? "" : (d.norm > 0 ? "+" : "") + d.norm.toFixed(1)}</span></div>`;
    }
  }
  setHTML(box, html);
}

// ---------------------------------------------------------------- history
function renderArms(arms) {
  const key = JSON.stringify(arms);
  if (key === lastArmsKey) return;
  lastArmsKey = key;
  if (!arms.length) {
    setHTML("arm-tabs", "");
    setHTML("history", `<p class="muted">No calibrations stored yet. Finish a calibration or import an existing LeRobot file with <code>so101-cal import</code>.</p>`);
    return;
  }
  if (!selectedArm || !arms.some((a) => a.arm_id === selectedArm)) selectedArm = arms[0].arm_id;
  setHTML("arm-tabs", arms.map((a) => `<button class="${a.arm_id === selectedArm ? "on" : ""}" data-arm="${esc(a.arm_id)}">${esc(a.arm_id)}<span class="role">${esc(a.role)} · ${a.versions}</span></button>`).join(""));
  loadHistory();
}

$("arm-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  selectedArm = b.dataset.arm;
  diffSel = { a: null, b: null };
  setHTML("diff", "");
  lastArmsKey = "";
  renderArms(state.arms);
});

async function loadHistory() {
  if (!selectedArm) return;
  const data = await api(`/api/arms/${encodeURIComponent(selectedArm)}/versions`);
  const active = data.arm.active;
  const rows = data.versions.slice().reverse().map((v) => {
    const c = v.checks;
    const counts = c.pass + c.warn + c.fail ? `<span class="p">${c.pass}✓</span><span class="w">${c.warn}!</span><span class="f">${c.fail}✕</span>` : `<span class="muted">—</span>`;
    return `<tr class="${v.version === active ? "active" : ""}">
      <td class="mono">v${v.version} ${v.version === active ? '<span class="pill">active</span>' : ""}</td>
      <td class="mono">${esc(v.created.replace("T", " ").replace("+00:00", "Z"))}</td>
      <td>${esc(v.source)}</td>
      <td class="counts">${counts}</td>
      <td>${esc(v.note || "")} <button class="ghost small" data-note="${v.version}" title="Edit note">✎</button></td>
      <td><input type="radio" name="da" value="${v.version}" ${diffSel.a === v.version ? "checked" : ""} aria-label="diff A"></td>
      <td><input type="radio" name="db" value="${v.version}" ${diffSel.b === v.version ? "checked" : ""} aria-label="diff B"></td>
      <td class="row">${v.version !== active ? `<button class="secondary small" data-activate="${v.version}">Activate</button>` : ""}
        <button class="ghost small" data-export="${v.version}">Export</button></td></tr>`;
  }).join("");
  setHTML("history", `<div class="table-wrap"><table>
    <thead><tr><th>Version</th><th>Created</th><th>Source</th><th>Checks</th><th>Note</th><th>A</th><th>B</th><th></th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <div class="diff-head"><button class="secondary small" id="diff-btn">Compare A → B</button>
    <span class="muted small">USB serial ${esc(data.arm.usb_serial || "unknown")} · last port ${esc(data.arm.port || "—")}</span></div>`);
}

$("history").addEventListener("change", (e) => {
  if (e.target.name === "da") diffSel.a = +e.target.value;
  if (e.target.name === "db") diffSel.b = +e.target.value;
});

$("history").addEventListener("click", async (e) => {
  const t = e.target.closest("button");
  if (!t) return;
  const arm = encodeURIComponent(selectedArm);
  try {
    if (t.dataset.activate) {
      if (!confirm(`Make v${t.dataset.activate} active and export it to LeRobot? The motors keep their current values until you run "so101-cal write" or LeRobot writes the file on connect.`)) return;
      const r = await api(`/api/arms/${arm}/activate`, { version: +t.dataset.activate });
      toast(`v${t.dataset.activate} active · exported to ${r.exported}`);
    } else if (t.dataset.export) {
      const r = await api(`/api/arms/${arm}/export`, { version: +t.dataset.export });
      toast(`Exported to ${r.exported}`);
    } else if (t.dataset.note) {
      const note = prompt("Note for this version:");
      if (note === null) return;
      await api(`/api/arms/${arm}/note`, { version: +t.dataset.note, note });
    } else if (t.id === "diff-btn") {
      return showDiff();
    }
    lastArmsKey = "";
    refresh();
    loadHistory();
  } catch (err) {
    toast(err.message);
  }
});

async function showDiff() {
  if (!diffSel.a) { toast("Pick version A (and optionally B; default is the active one)."); return; }
  const q = `a=${diffSel.a}${diffSel.b ? `&b=${diffSel.b}` : ""}`;
  const d = await api(`/api/arms/${encodeURIComponent(selectedArm)}/diff?${q}`);
  if (!d.rows.length) { setHTML("diff", `<p class="muted">v${d.a} and v${d.b} are identical.</p>`); return; }
  setHTML("diff", `<h2 style="margin-top:8px">v${d.a} → v${d.b}</h2><div class="table-wrap"><table>
    <thead><tr><th>Joint</th><th>Field</th><th>v${d.a}</th><th>v${d.b}</th><th>Δ ticks</th><th>Δ deg</th></tr></thead><tbody>
    ${d.rows.map((r) => `<tr><td>${esc(r.joint)}</td><td class="mono">${esc(r.field)}</td><td class="mono">${esc(r.a)}</td><td class="mono">${esc(r.b)}</td>
      <td class="mono delta">${r.delta === null ? "" : (r.delta > 0 ? "+" : "") + r.delta}</td>
      <td class="mono">${r.delta === null ? "" : ((r.delta * 360) / 4096).toFixed(1)}</td></tr>`).join("")}
    </tbody></table></div>`);
}

// ---------------------------------------------------------------- polling
let formRendered = false;
let lastPorts = "";

async function refresh() {
  try {
    state = await api("/api/state");
  } catch (err) {
    return;
  }
  $("mock-badge").hidden = !state.mock;
  $("store-path").textContent = `history: ${state.store}`;
  $("lerobot-root").textContent = `LeRobot cache: ${state.lerobot_root}`;
  const ports = JSON.stringify(state.ports);
  if (!formRendered || (ports !== lastPorts && !document.activeElement.closest("#start-form"))) {
    renderForm();
    formRendered = true;
    lastPorts = ports;
  }
  renderSession(state.session);
  renderLive(state.session);
  renderArms(state.arms);
}

function loop() {
  const live = state && state.session && !state.session.finished;
  refresh().finally(() => setTimeout(loop, live ? 120 : 800));
}
loop();

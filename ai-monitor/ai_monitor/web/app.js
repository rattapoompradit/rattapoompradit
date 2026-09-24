const REFRESH_MS = 10000;
const MODAL_AUTOCLOSE_MS = 20000;
const LABEL = { up: "ปกติ", degraded: "มีปัญหา", down: "ล่ม", unknown: "ไม่ทราบ" };
const CODE = { up: "ONLINE", degraded: "WARNING", down: "OFFLINE", unknown: "STANDBY" };
// Local models: being idle (READY) is normal, so it gets its own calm state instead of a status color.
const LOCAL = {
  active: { code: "ACTIVE", label: "โหลดอยู่ใน VRAM" },
  ready: { code: "READY", label: "พร้อมใช้" },
  missing: { code: "MISSING", label: "ไม่พบโมเดล" },
  starting: { code: "STARTING", label: "กำลังเปิด Ollama" },
};
const localState = (p) => (p.type === "ollama" && p.status !== "down" && p.extra ? LOCAL[p.extra.state] : null);
const codeFor = (p) => (localState(p) || { code: CODE[p.status] }).code;
const statusClass = (p) => (p.type === "ollama" && p.status === "up" && p.extra && p.extra.state === "ready" ? "s-ready" : `s-${p.status}`);

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const num = (n) => (n ?? 0).toLocaleString("en-US");
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" });
const clockSec = (ts) => new Date(ts * 1000).toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const gb = (mb) => (mb / 1024).toFixed(1);
const level = (v, warn, crit) => (v == null ? "unknown" : v >= crit ? "down" : v >= warn ? "degraded" : "up");

function ago(ts) {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "เมื่อสักครู่";
  if (s < 3600) return `${Math.floor(s / 60)} นาที`;
  if (s < 86400) return `${Math.floor(s / 3600)} ชม.`;
  return `${Math.floor(s / 86400)} วัน`;
}

function duration(sec) {
  if (sec == null) return "-";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d}ว ${h}ชม.` : h ? `${h}ชม. ${m}น.` : `${m} นาที`;
}

function resetText(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const sameDay = d.toDateString() === new Date().toDateString();
  if (ts - Date.now() / 1000 > 7 * 86400) return "รีเซ็ต " + d.toLocaleDateString("th-TH", { day: "numeric", month: "short" });
  return "รีเซ็ต " + (sameDay ? clock(ts) : d.toLocaleDateString("th-TH", { weekday: "short", hour: "2-digit", minute: "2-digit" }));
}

const isStale = (p) => p.checked_at && Date.now() / 1000 - p.checked_at > 3 * (p.interval_s || 60) + 30;

/* ---------- building blocks ---------- */

const stat = (label, value, unit = "") => `<span class="stat"><em>${label}</em><b>${value}</b>${unit}</span>`;

function sparkline(values, cls = "spark") {
  const pts = values.map((v, i) => [i, v]).filter(([, v]) => v != null);
  if (pts.length < 2) return "";
  const max = Math.max(...pts.map(([, v]) => v)) * 1.15 || 1, n = values.length - 1;
  const xy = pts.map(([i, v]) => `${((i / n) * 100).toFixed(2)},${(30 - (v / max) * 28).toFixed(2)}`);
  const first = xy[0].split(",")[0], lastX = xy[xy.length - 1].split(",")[0];
  return `<svg class="${cls}" viewBox="0 0 100 30" preserveAspectRatio="none">
    <polygon class="area" points="${first},30 ${xy.join(" ")} ${lastX},30"/><polyline points="${xy.join(" ")}"/></svg>`;
}

function bar(label, pct, right, lv) {
  const w = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  return `<div class="bar lv-${lv}"><div class="bar-head"><span>${label}</span><span>${right}</span></div>
    <div class="track"><div class="fill" style="width:${w}%"></div></div></div>`;
}

function usageBars(u) {
  if (u.error) return `<div class="usage-error" title="${esc(u.error)}">⚠ โควตา: ${esc(u.error)}</div>`;
  const big = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(2)}B` : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : `${Math.round(n)}`);
  return `<div class="bars">${(u.bars || []).map((b) => {
    const label = b.used != null && b.total ? `${esc(b.label)} · ${big(b.used)} / ${big(b.total)}` : esc(b.label);
    return bar(label, b.used_pct, `<b>${b.used_pct}%</b>${resetText(b.resets_at)}`, level(b.used_pct, 70, 90));
  }).join("")}${u.note ? `<div class="usage-note" title="${esc(u.note)}">${esc(u.note)}</div>` : ""}</div>`;
}

function gauge(label, value, unit, pct, lv, sub = "") {
  const arc = (75 * Math.max(0, Math.min(100, pct ?? 0))) / 100;
  return `<div class="gauge lv-${lv}">
    <svg viewBox="0 0 100 100"><circle class="g-track" cx="50" cy="50" r="42" pathLength="100"/>
      <circle class="g-fill" cx="50" cy="50" r="42" pathLength="100" style="stroke-dasharray:${arc.toFixed(1)} 100"/></svg>
    <div class="g-val"><b>${value ?? "--"}</b><small>${unit}</small></div>
    <em>${label}</em>${sub ? `<span class="g-sub">${sub}</span>` : ""}</div>`;
}

function head(p, tag = p.group) {
  return `<div class="head"><span class="dot"></span><span class="name">${esc(p.name)}</span>
    ${tag ? `<span class="tag">${esc(String(tag).toUpperCase())}</span>` : ""}</div>`;
}

const stateLine = (p) =>
  `<div class="state"><span class="code">${codeFor(p)}</span><span class="th">${(localState(p) || { label: LABEL[p.status] }).label}${p.since ? ` · ${ago(p.since)}` : ""}</span></div>`;

function unloadText(exp) {
  const s = exp - Date.now() / 1000;
  if (s > 86400) return "ค้างอยู่ใน VRAM ตลอด";
  if (s <= 0) return "กำลังปล่อย VRAM…";
  return `ปล่อย VRAM ใน <b>${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}</b>`;
}

function speedBlock(s) {
  if (!s || s.tok_s == null) return "";
  const lv = level(-s.tok_s, -30, -10); // >=30 tok/s green, 10-30 yellow, <10 red
  const when = Date.now() / 1000 - s.at < 60 ? "เพิ่งวัด" : `วัดไว้ ${ago(s.at)}ที่แล้ว`;
  const more = [s.prompt_tok_s != null ? `อ่าน prompt ${num(Math.round(s.prompt_tok_s))} tok/s` : "",
    s.load_s != null ? `โหลด ${s.load_s} วิ` : "", when].filter(Boolean).join(" · ");
  return `<div class="speed lv-${lv}"><div><b>${s.tok_s}</b><span>tok/s</span></div><small title="${esc(more)}">${esc(more)}</small></div>`;
}

function localInner(p) {
  const x = p.extra || {};
  const spec = [x.params, x.quant].filter(Boolean).join(" · ");
  const api = p.latency_ms != null ? stat("API", num(p.latency_ms), "ms") : "";
  let body;
  if (x.state === "active" && p.status !== "down") {
    const gpu = x.gpu_pct ?? 100;
    body = `<div class="split">
        <div class="split-head"><span>GPU <b>${gpu}%</b></span>${gpu < 100
          ? `<span class="warn">CPU <b>${100 - gpu}%</b> · ช้าลง</span>` : `<span>VRAM <b>${x.vram_gb} GB</b></span>`}</div>
        <div class="split-track"><div class="split-gpu" style="width:${gpu}%"></div><div class="split-cpu" style="width:${100 - gpu}%"></div></div>
      </div>
      ${speedBlock(x.speed)}
      <div class="stats">${x.context ? stat("CTX", num(x.context)) : ""}${api}</div>
      ${x.expires_at ? `<div class="unload" data-exp="${x.expires_at}">${unloadText(x.expires_at)}</div>` : ""}`;
  } else if (x.state === "ready" && p.status !== "down") {
    body = `${speedBlock(x.speed)}<div class="stats">${x.disk_gb ? stat("SIZE", x.disk_gb, "GB") : ""}${api}${p.uptime_24h != null ? stat("UP 24H", `${p.uptime_24h}%`) : ""}</div>
      <div class="idle-note">◇ ไม่ได้ใช้ VRAM ตอนนี้ · โหลดเองเมื่อมีการเรียกใช้</div>`;
  } else if (x.state === "missing" && p.status !== "down") {
    body = `<div class="idle-note">โมเดลที่มีใน Ollama (แก้ model_hint ใน config.yaml):</div>
      <div class="chips">${(x.available || []).slice(0, 6).map((n) => `<span class="chip idle">${esc(n)}</span>`).join("") || `<span class="chip idle">ยังไม่มีโมเดล</span>`}</div>`;
  } else if (x.state === "starting") {
    body = `<div class="idle-note">◇ เปิด <b>ollama serve</b> ในพื้นหลังแล้ว ปกติใช้เวลาไม่กี่วินาที</div>`;
  } else {
    body = `<div class="idle-note">เปิด Ollama แล้วรอสักครู่ หรือรัน check.bat เพื่อดูสาเหตุ</div>`;
  }
  return `${head(p)}${stateLine(p)}
    <div class="detail" title="${esc(p.detail)}">${esc(p.detail)}</div>
    ${x.model ? `<div class="sub" title="${esc(x.model)}">${esc(x.model)}${spec ? ` · ${esc(spec)}` : ""}</div>` : ""}
    ${body}
    ${x.ollama_version ? `<div class="foot">OLLAMA ${esc(x.ollama_version)} · ${esc(x.server)}</div>` : ""}`;
}

/* ---------- cards ---------- */

function providerInner(p) {
  const x = p.extra || {};
  const stats = [];
  if (p.latency_ms != null) stats.push(stat("LAT", num(p.latency_ms), "ms"));
  if (x.quota_pct != null) stats.push(stat("QUOTA", `${x.quota_pct}%`));
  if (x.vram_gb != null) stats.push(stat("VRAM", x.vram_gb, "GB"));
  if (p.uptime_24h != null) stats.push(stat("UP 24H", `${p.uptime_24h}%`));
  const sub = x.incident ? `<div class="sub warn" title="${esc(x.incident)}">⚠ ${esc(x.incident)}</div>`
    : x.model ? `<div class="sub" title="${esc(x.model)}">${esc(x.model)}</div>`
    : x.usage && x.usage.plan ? `<div class="sub">แพ็กเกจ ${esc(String(x.usage.plan).toUpperCase())}</div>` : "";
  return `${head(p)}${stateLine(p)}
    <div class="detail" title="${esc(p.detail)}">${esc(p.detail)}</div>${sub}
    <div class="stats">${stats.join("")}</div>
    ${x.usage ? usageBars(x.usage) : sparkline(p.history || [])}`;
}

function gpuInner(p, providers) {
  const x = p.extra || {}, gpus = x.gpus || [], g = gpus[0];
  const tag = gpus.length > 1 ? `${p.group} · ${gpus.length} GPU` : p.group;
  if (!g) return `${head(p, tag)}${stateLine(p)}<div class="detail">${esc(p.detail)}</div>`;
  const vram = x.vram_pct;
  const powerPct = g.power_w != null && g.power_limit_w ? (g.power_w * 100) / g.power_limit_w : null;
  const loaded = providers.filter((q) => q.type === "ollama" && q.extra && q.extra.loaded);
  return `${head(p, tag)}
    <div class="gpu-name" title="${esc(g.name)}">${esc(g.name.replace(/^NVIDIA\s+/i, ""))}<span class="th"> · ${CODE[p.status]}</span></div>
    <div class="gpu-body">
      <div class="gauges">
        ${gauge("TEMP", g.temp != null ? Math.round(g.temp) : null, "°C", g.temp, level(g.temp, 80, 90))}
        ${gauge("VRAM", vram, "%", vram, level(vram, 85, 95), g.mem_total_mb ? `${gb(g.mem_used_mb)} / ${gb(g.mem_total_mb)} GB` : "")}
      </div>
      <div class="meters">
        ${bar("UTIL", g.util, g.util != null ? `<b>${Math.round(g.util)}%</b>` : "N/A", level(g.util, 90, 101))}
        ${bar("POWER", powerPct, g.power_w != null ? `<b>${Math.round(g.power_w)}</b> W` : "N/A", level(powerPct, 85, 97))}
        ${bar("FAN", g.fan, g.fan != null ? `<b>${Math.round(g.fan)}%</b>` : "N/A", level(g.fan, 85, 101))}
      </div>
    </div>
    <div class="chips">${loaded.length ? loaded.map((q) =>
      `<span class="chip ok" title="${esc(q.extra.model)}">${esc(q.extra.model)} · ${q.extra.vram_gb} GB</span>`).join("")
      : `<span class="chip idle">ไม่มีโมเดลโหลดใน VRAM</span>`}</div>`;
}

function tile(label, value, small = "") {
  return `<div class="tile"><em>${label}</em><b title="${esc(value)}">${esc(value)}</b>${small ? `<small title="${esc(small)}">${esc(small)}</small>` : ""}</div>`;
}

function hermesInner(p) {
  const x = p.extra || {}, s = x.sessions, c = x.cron;
  const platforms = Object.entries(x.platforms || {}).map(([name, v]) =>
    `<span class="chip ${v.state === "connected" ? "ok" : "bad"}" title="${esc(v.error || v.state)}">${esc(name)}</span>`).join("");
  const errors = x.errors || [];
  return `<div class="head">
      <div class="core"><svg viewBox="0 0 100 100">
        <circle class="glow" cx="50" cy="50" r="36"/><circle class="outer" cx="50" cy="50" r="48"/><circle class="inner" cx="50" cy="50" r="40"/>
      </svg><span>H</span></div>
      <div class="hermes-title"><span class="name">${esc(p.name)}</span>
        <div class="state"><span class="code">${CODE[p.status]}</span><span class="th">${LABEL[p.status]}</span></div></div>
      ${x.version ? `<span class="tag">v${esc(x.version)}</span>` : ""}
    </div>
    <div class="detail" title="${esc(p.detail)}">${esc(p.detail)}</div>
    ${platforms ? `<div class="chips">${platforms}</div>` : ""}
    <div class="tiles">
      ${tile("GATEWAY", x.pid ? duration(x.uptime_s) : "OFF", x.pid ? `PID ${x.pid}` : "ไม่ได้รัน")}
      ${tile("MODEL", x.model || "-", x.provider || "")}
      ${tile("AGENTS", x.active_agents ?? "-", "กำลังทำงาน")}
      ${tile("SESSIONS", s ? s.today : "-", "วันนี้")}
      ${tile("TOKENS", s ? num(s.tokens_today) : "-", s && s.last_at ? `ล่าสุด ${ago(s.last_at)}${s.last_source ? " · " + s.last_source : ""}` : "")}
      ${tile("CRON", c ? `${c.enabled}/${c.total}` : "-", c && c.next_at ? `ถัดไป ${clock(c.next_at)} ${c.next_name || ""}` : c && c.failed ? `ล้ม ${c.failed}` : "")}
    </div>
    <div class="console ${errors.length ? "" : "none"}">${errors.length
      ? errors.map((e) => `<div title="${esc(e)}">${esc(e)}</div>`).join("") : "<div>ไม่มี error ล่าสุด</div>"}</div>`;
}

function inner(p, providers) {
  if (p.type === "hermes") return hermesInner(p);
  if (p.type === "gpu") return gpuInner(p, providers);
  if (p.type === "ollama") return localInner(p);
  return providerInner(p);
}

function card(p, providers) {
  const stale = isStale(p);
  return `<article class="card type-${p.type} ${statusClass(p)}${stale ? " stale" : ""}" data-id="${esc(p.id)}">
    ${inner(p, providers)}${stale ? `<span class="stale-badge">ข้อมูลเก่า ${ago(p.checked_at)}</span>` : ""}</article>`;
}

/* ---------- top bar ---------- */

function summary(providers) {
  const count = (st) => providers.filter((p) => p.status === st).length;
  return ["up", "degraded", "down"].map((st) =>
    `<div class="pill" style="--c: var(--${st})"><b>${count(st)}</b><span>${LABEL[st]}</span></div>`).join("");
}

let tickerKey = null;
function renderTicker(events) {
  const key = events.map((e) => `${e.ts}${e.to}`).join();
  if (key === tickerKey) return; // keep the marquee running smoothly between refreshes
  tickerKey = key;
  const el = document.getElementById("ticker");
  if (!events.length) {
    el.innerHTML = `<div class="ticker-idle">◆ ยังไม่มีการเปลี่ยนสถานะ — กำลังเฝ้าดูทุกระบบ</div>`;
    return;
  }
  const items = events.map((e) => `<span class="tk"><time>${clock(e.ts)}</time><b>${esc(e.name)}</b>
    <span class="to-${e.from}">${CODE[e.from] || "-"}</span><i>→</i><span class="to-${e.to}">${CODE[e.to]}</span>
    <small>${esc(e.detail)}</small></span>`).join("");
  el.innerHTML = `<div class="ticker-track" style="--dur:${Math.max(24, events.length * 10)}s">${items}${items}</div>`;
}

/* ---------- detail overlay (tap a card; the Xeneon Edge is a touch screen) ---------- */

let modalTimer = null;
function openModal(id) {
  const p = last.providers.find((q) => q.id === id);
  if (!p) return;
  const unit = p.type === "gpu" ? "°C" : "ms";
  const hist = (p.history || []).filter((v) => v != null);
  const rows = [
    ["สถานะ", `${codeFor(p)} · ${(localState(p) || { label: LABEL[p.status] }).label}${p.since ? ` ตั้งแต่ ${clock(p.since)}` : ""}`],
    ["รายละเอียด", p.detail],
    p.extra && p.extra.incident ? ["เหตุการณ์", p.extra.incident] : null,
    ["เช็คล่าสุด", p.checked_at ? `${clockSec(p.checked_at)} · ทุก ${p.interval_s} วินาที` : "-"],
    p.uptime_24h != null ? ["Uptime 24 ชม.", `${p.uptime_24h}% (เช็คสำเร็จ ไม่ใช่โควตา)`] : null,
    p.extra && p.extra.usage && p.extra.usage.console_url ? ["โควตาจริง", p.extra.usage.console_url] : null,
    hist.length ? ["ประวัติ", `ต่ำสุด ${Math.min(...hist)} · สูงสุด ${Math.max(...hist)} ${unit}`] : null,
  ].filter(Boolean);
  const modal = document.getElementById("modal");
  modal.innerHTML = `<div class="card modal-card type-${p.type} ${statusClass(p)}">
    <div class="modal-main">${inner(p, last.providers)}</div>
    <aside class="modal-side">
      <dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      ${p.type === "ollama" && p.status !== "down" ? `<button class="bench" data-bench="${esc(p.id)}">⚡ วัดความเร็วตอนนี้</button>
        <div class="bench-note">ถ้าโมเดลยังไม่โหลด จะโหลดเข้า VRAM ก่อน (รอสักครู่)</div>` : ""}
      ${sparkline(p.history || [], "spark big")}
      <div class="hint">แตะที่ใดก็ได้เพื่อปิด</div>
    </aside></div>`;
  modal.hidden = false;
  clearTimeout(modalTimer);
  modalTimer = setTimeout(closeModal, MODAL_AUTOCLOSE_MS);
}

function closeModal() {
  document.getElementById("modal").hidden = true;
  clearTimeout(modalTimer);
}

async function runBench(btn) {
  const id = btn.dataset.bench;
  btn.disabled = true;
  btn.textContent = "⏳ กำลังวัด… (โหลดโมเดลอาจใช้เวลา)";
  clearTimeout(modalTimer);
  try {
    const r = await fetch(`/api/bench/${encodeURIComponent(id)}`, { method: "POST" });
    const res = await r.json();
    if (!r.ok || res.error) throw new Error(res.error || res.detail || r.status);
    await refresh();
    openModal(id);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = `วัดไม่สำเร็จ: ${err.message} — แตะเพื่อลองใหม่`;
    modalTimer = setTimeout(closeModal, MODAL_AUTOCLOSE_MS);
  }
}

document.addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-bench]");
  if (btn) {
    if (!btn.disabled) runBench(btn);
    return;
  }
  if (!document.getElementById("modal").hidden) return closeModal();
  const el = ev.target.closest("[data-id]");
  if (el) openModal(el.dataset.id);
});
document.addEventListener("keydown", (ev) => ev.key === "Escape" && closeModal());

/* ---------- Hermes Router status bar (monitoring only) ---------- */

const ROUTER_REFRESH_MS = 3000;
const slug = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-");

function renderRouter(r) {
  const el = document.getElementById("router");
  if (!r || !r.available) {
    el.className = "router-bar na";
    el.innerHTML = `<div class="rrow"><span class="rk">ROUTER</span><b class="rv">N/A</b></div>
      <div class="rrow"><span class="reason" title="${esc(r && r.note)}">${esc((r && r.note) || "ยังไม่มีข้อมูล routing")}</span></div>`;
    return;
  }
  const status = r.status || "N/A";
  el.className = `router-bar rs-${slug(status)}`;
  el.innerHTML = `<div class="rrow">
      <span class="rk">ROUTER</span><b class="route rt-${slug(r.route)}">${esc(r.route || "N/A")}</b>
      <span class="rk">MODEL</span><b class="rv" title="${esc(r.model || "N/A")}">${esc(r.model || "N/A")}</b>
      <span class="rstat"><i></i>${esc(status)}</span>
    </div>
    <div class="rrow">
      <span class="rk">REASON</span><span class="reason" title="${esc(r.reason || "")}">${esc(r.reason || "N/A")}</span>
      <small title="${esc(r.source || "")}">${r.at ? ago(r.at) + (Date.now() / 1000 - r.at < 60 ? "" : "ที่แล้ว") : ""}</small>
    </div>`;
}

let routerLast = null;
async function refreshRouter() {
  try {
    const r = await fetch("/api/router", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    routerLast = await r.json();
  } catch {
    routerLast = { available: false, note: "เชื่อมต่อ AI Monitor ไม่ได้" };
  }
  renderRouter(routerLast);
}

/* ---------- render loop ---------- */

let last = { providers: [], events: [] };

function render(data, stale = false) {
  const providers = data.providers;
  const hermes = providers.find((p) => p.type === "hermes");
  const hermesEl = document.getElementById("hermes");
  hermesEl.hidden = !hermes;
  if (hermes) {
    const st = isStale(hermes);
    hermesEl.className = `card hermes s-${hermes.status}${st ? " stale" : ""}`;
    hermesEl.dataset.id = hermes.id;
    hermesEl.innerHTML = hermesInner(hermes) + (st ? `<span class="stale-badge">ข้อมูลเก่า ${ago(hermes.checked_at)}</span>` : "");
  }
  document.getElementById("grid").innerHTML = providers.filter((p) => p !== hermes).map((p) => card(p, providers)).join("");
  document.getElementById("summary").innerHTML = summary(providers);
  renderTicker(data.events || []);
  const link = document.getElementById("link");
  link.className = stale ? "link lost" : "link";
  link.textContent = stale ? "● LINK LOST" : data.generated_at ? `● SYNC ${clock(data.generated_at)}` : "";
}

function tick() {
  const now = new Date();
  document.getElementById("clock").textContent = now.toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  document.getElementById("date").textContent = now.toLocaleDateString("th-TH", { weekday: "short", day: "numeric", month: "short" });
  document.querySelectorAll(".unload[data-exp]").forEach((el) => { el.innerHTML = unloadText(+el.dataset.exp); });
  const h = now.getHours();
  document.documentElement.classList.toggle("night", h >= 23 || h < 7); // dim the always-on screen at night
}

async function refresh() {
  try {
    const r = await fetch("/api/status", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    last = await r.json();
    render(last);
  } catch {
    render(last, true);
  }
}

tick();
refresh();
refreshRouter();
setInterval(refreshRouter, ROUTER_REFRESH_MS);
setInterval(refresh, REFRESH_MS);
setInterval(tick, 1000);

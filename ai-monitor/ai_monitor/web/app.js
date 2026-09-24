const REFRESH_MS = 10000;
const LABEL = { up: "ปกติ", degraded: "มีปัญหา", down: "ล่ม", unknown: "ไม่ทราบ" };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function ago(ts) {
  if (!ts) return "-";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "เมื่อสักครู่";
  if (s < 3600) return `${Math.floor(s / 60)} นาทีที่แล้ว`;
  if (s < 86400) return `${Math.floor(s / 3600)} ชม.ที่แล้ว`;
  return `${Math.floor(s / 86400)} วันที่แล้ว`;
}

function duration(sec) {
  if (sec == null) return "-";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d} วัน ${h} ชม.` : h ? `${h} ชม. ${m} นาที` : `${m} นาที`;
}

const clock = (ts) => new Date(ts * 1000).toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" });
const num = (n) => (n ?? 0).toLocaleString("en-US");

function sparkline(values) {
  const pts = values.map((v, i) => [i, v]).filter(([, v]) => v != null);
  if (pts.length < 2) return "";
  const max = Math.max(...pts.map(([, v]) => v)) * 1.15 || 1, n = values.length - 1;
  const xy = pts.map(([i, v]) => `${((i / n) * 100).toFixed(2)},${(30 - (v / max) * 28).toFixed(2)}`);
  const first = xy[0].split(",")[0], last = xy[xy.length - 1].split(",")[0];
  return `<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none">
    <polygon class="area" points="${first},30 ${xy.join(" ")} ${last},30"/>
    <polyline points="${xy.join(" ")}"/></svg>`;
}

function providerCard(p) {
  const x = p.extra || {};
  const meta = [];
  if (p.latency_ms != null) meta.push(`<span><b>${num(p.latency_ms)}</b> ms</span>`);
  if (x.quota_pct != null) meta.push(`<span>quota <b>${x.quota_pct}%</b></span>`);
  if (x.vram_gb != null) meta.push(`<span>VRAM <b>${x.vram_gb} GB</b></span>`);
  if (p.uptime_24h != null) meta.push(`<span>24 ชม. <b>${p.uptime_24h}%</b></span>`);
  const sub = x.incident ? `⚠ ${x.incident}` : x.model || "";
  return `<article class="card status-${p.status}">
    <div class="head"><span class="dot"></span><span class="name">${esc(p.name)}</span>
      ${p.group ? `<span class="group">${esc(p.group)}</span>` : ""}</div>
    <div class="state">${LABEL[p.status]}<small>${p.since ? ago(p.since) : ""}</small></div>
    <div class="detail" title="${esc(p.detail)}">${esc(p.detail)}</div>
    ${sub ? `<div class="detail" title="${esc(sub)}">${esc(sub)}</div>` : ""}
    <div class="meta">${meta.join("")}</div>
    ${sparkline(p.history || [])}
  </article>`;
}

function hermesCard(p) {
  const x = p.extra || {}, s = x.sessions, c = x.cron;
  const platforms = Object.entries(x.platforms || {}).map(([name, v]) =>
    `<span class="chip ${v.state === "connected" ? "ok" : "bad"}" title="${esc(v.error || v.state)}">${esc(name)}</span>`).join("");
  const rows = [
    ["Gateway", x.pid ? `PID ${x.pid} · รันมา ${duration(x.uptime_s)}` : "ไม่ได้รัน"],
    ["Model", [x.model, x.provider].filter(Boolean).join(" · ") || "-"],
    ["Agents", x.active_agents != null ? `${x.active_agents} ตัวกำลังทำงาน` : "-"],
    ["Sessions", s ? `วันนี้ ${s.today} · ${num(s.tokens_today)} tokens` : "-"],
    ["ล่าสุด", s && s.last_at ? `${ago(s.last_at)}${s.last_source ? ` (${s.last_source})` : ""}` : "-"],
    ["Cron", c ? `${c.enabled}/${c.total} งาน${c.next_at ? ` · ถัดไป ${clock(c.next_at)} ${c.next_name || ""}` : ""}${c.failed ? ` · ล้ม ${c.failed}` : ""}` : "-"],
  ];
  const errors = x.errors || [];
  return `<div class="head"><span class="dot"></span><span class="name">${esc(p.name)}</span>
      ${x.version ? `<span class="group">v${esc(x.version)}</span>` : ""}</div>
    <div class="state">${LABEL[p.status]}<small>${esc(p.detail)}</small></div>
    ${platforms ? `<div class="chips">${platforms}</div>` : ""}
    <dl class="rows">${rows.map(([k, v]) => `<dt>${k}</dt><dd title="${esc(v)}">${esc(v)}</dd>`).join("")}</dl>
    <div class="errors ${errors.length ? "" : "none"}">${errors.length
      ? errors.map((e) => `<div title="${esc(e)}">${esc(e)}</div>`).join("") : "ไม่มี error ล่าสุด"}</div>`;
}

function eventsCard(events, generatedAt, stale) {
  const items = events.map((e) =>
    `<li>${clock(e.ts)} <b>${esc(e.name)}</b> <span class="to-${e.from}">${LABEL[e.from] || "-"}</span> → <span class="to-${e.to}">${LABEL[e.to]}</span> ${esc(e.detail)}</li>`).join("");
  return `<article class="card events">
    <div class="head"><span class="clock" id="clock"></span><span class="group">อัปเดต ${generatedAt ? clock(generatedAt) : "-"}</span></div>
    ${stale ? `<div class="stale">⚠ เชื่อมต่อ AI Monitor ไม่ได้</div>` : ""}
    <ul>${items || "<li>ยังไม่มีการเปลี่ยนสถานะ</li>"}</ul>
  </article>`;
}

let last = { providers: [], events: [] };

function render(data, stale = false) {
  const hermes = data.providers.find((p) => p.type === "hermes");
  const hermesEl = document.getElementById("hermes");
  hermesEl.hidden = !hermes;
  if (hermes) {
    hermesEl.className = `card hermes status-${hermes.status}`;
    hermesEl.innerHTML = hermesCard(hermes);
  }
  document.getElementById("grid").innerHTML =
    data.providers.filter((p) => p !== hermes).map(providerCard).join("") +
    eventsCard(data.events, data.generated_at, stale);
  tick();
}

function tick() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
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

refresh();
setInterval(refresh, REFRESH_MS);
setInterval(tick, 1000);

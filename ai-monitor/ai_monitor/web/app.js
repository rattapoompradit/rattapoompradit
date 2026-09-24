const REFRESH_MS = 10000;
const LABEL = { up: "ปกติ", degraded: "มีปัญหา", down: "ล่ม", unknown: "ไม่ทราบ" };
const CODE = { up: "ONLINE", degraded: "WARNING", down: "OFFLINE", unknown: "STANDBY" };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const num = (n) => (n ?? 0).toLocaleString("en-US");
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" });

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
  return "รีเซ็ต " + (sameDay ? clock(ts) : d.toLocaleDateString("th-TH", { weekday: "short", hour: "2-digit", minute: "2-digit" }));
}

const stat = (label, value, unit = "") => `<span class="stat"><em>${label}</em><b>${value}</b>${unit}</span>`;

function sparkline(values) {
  const pts = values.map((v, i) => [i, v]).filter(([, v]) => v != null);
  if (pts.length < 2) return "";
  const max = Math.max(...pts.map(([, v]) => v)) * 1.15 || 1, n = values.length - 1;
  const xy = pts.map(([i, v]) => `${((i / n) * 100).toFixed(2)},${(30 - (v / max) * 28).toFixed(2)}`);
  const first = xy[0].split(",")[0], last = xy[xy.length - 1].split(",")[0];
  return `<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none">
    <polygon class="area" points="${first},30 ${xy.join(" ")} ${last},30"/><polyline points="${xy.join(" ")}"/></svg>`;
}

function usageBars(u) {
  if (u.error) return `<div class="usage-error" title="${esc(u.error)}">⚠ โควตา: ${esc(u.error)}</div>`;
  return `<div class="bars">${(u.bars || []).map((b) => {
    const pct = Math.max(0, Math.min(100, b.used_pct));
    const lv = pct >= 90 ? "down" : pct >= 70 ? "degraded" : "up";
    return `<div class="bar lv-${lv}"><div class="bar-head"><span>${esc(b.label)}</span>
      <span><b>${b.used_pct}%</b>${resetText(b.resets_at)}</span></div>
      <div class="track"><div class="fill" style="width:${pct}%"></div></div></div>`;
  }).join("")}</div>`;
}

function providerCard(p) {
  const x = p.extra || {};
  const stats = [];
  if (p.latency_ms != null) stats.push(stat("LAT", num(p.latency_ms), "ms"));
  if (x.quota_pct != null) stats.push(stat("QUOTA", `${x.quota_pct}%`));
  if (x.vram_gb != null) stats.push(stat("VRAM", x.vram_gb, "GB"));
  if (p.uptime_24h != null) stats.push(stat("24H", `${p.uptime_24h}%`));
  const sub = x.incident ? `<div class="sub warn" title="${esc(x.incident)}">⚠ ${esc(x.incident)}</div>`
    : x.model ? `<div class="sub" title="${esc(x.model)}">${esc(x.model)}</div>`
    : x.usage && x.usage.plan ? `<div class="sub">แพ็กเกจ ${esc(String(x.usage.plan).toUpperCase())}</div>` : "";
  return `<article class="card s-${p.status}">
    <div class="head"><span class="dot"></span><span class="name">${esc(p.name)}</span>
      ${p.group ? `<span class="tag">${esc(p.group.toUpperCase())}</span>` : ""}</div>
    <div class="state"><span class="code">${CODE[p.status]}</span><span class="th">${LABEL[p.status]}${p.since ? ` · ${ago(p.since)}` : ""}</span></div>
    <div class="detail" title="${esc(p.detail)}">${esc(p.detail)}</div>
    ${sub}
    <div class="stats">${stats.join("")}</div>
    ${x.usage ? usageBars(x.usage) : sparkline(p.history || [])}
  </article>`;
}

function tile(label, value, small = "") {
  return `<div class="tile"><em>${label}</em><b title="${esc(value)}">${esc(value)}</b>${small ? `<small title="${esc(small)}">${esc(small)}</small>` : ""}</div>`;
}

function hermesCard(p) {
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

function eventsCard(events) {
  const items = events.map((e) => `<li><time>${clock(e.ts)}</time><div><b>${esc(e.name)}</b>
    <span class="to-${e.from}">${CODE[e.from] || "-"}</span> → <span class="to-${e.to}">${CODE[e.to]}</span></div>
    <span></span><div title="${esc(e.detail)}">${esc(e.detail)}</div></li>`).join("");
  return `<article class="card events">
    <div class="head"><span class="dot"></span><span class="name">EVENT LOG</span></div>
    <ul>${items || "<li><span></span><div>ยังไม่มีการเปลี่ยนสถานะ</div></li>"}</ul>
  </article>`;
}

function summary(providers) {
  const count = (st) => providers.filter((p) => p.status === st).length;
  return ["up", "degraded", "down"].map((st) =>
    `<div class="pill" style="--c: var(--${st})"><b>${count(st)}</b><span>${LABEL[st]}</span></div>`).join("");
}

let last = { providers: [], events: [] };

function render(data, stale = false) {
  const hermes = data.providers.find((p) => p.type === "hermes");
  const hermesEl = document.getElementById("hermes");
  hermesEl.hidden = !hermes;
  if (hermes) {
    hermesEl.className = `card hermes s-${hermes.status}`;
    hermesEl.innerHTML = hermesCard(hermes);
  }
  document.getElementById("grid").innerHTML =
    data.providers.filter((p) => p !== hermes).map(providerCard).join("") + eventsCard(data.events);
  document.getElementById("summary").innerHTML = summary(data.providers);
  const link = document.getElementById("link");
  link.className = stale ? "link lost" : "link";
  link.textContent = stale ? "● LINK LOST" : data.generated_at ? `● SYNC ${clock(data.generated_at)}` : "";
}

function tick() {
  const now = new Date();
  document.getElementById("clock").textContent = now.toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  document.getElementById("date").textContent = now.toLocaleDateString("th-TH", { weekday: "short", day: "numeric", month: "short" });
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
setInterval(refresh, REFRESH_MS);
setInterval(tick, 1000);

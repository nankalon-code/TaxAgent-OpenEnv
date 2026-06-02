/* ═══════════════════════════════════════════════════════════
   TaxAgent-OpenEnv · eBPF Sandbox Dashboard · app.js
   Simulates the live eBPF event stream for demo purposes.
   In production, replace simulateEvent() with a WebSocket
   or SSE connection to harness.py's Flask/FastAPI server.
   ═══════════════════════════════════════════════════════════ */

"use strict";

// ── State ────────────────────────────────────────────────────
const state = {
  total: 0,
  writes: 0,
  reads: 0,
  events: [],           // full log
  filterMode: "all",
  running: false,
  simTimer: null,
  agentPid: null,
  scenario: null,
  pathCounts: { tmp: 0, etc: 0, proc: 0, home: 0, other: 0 },
  startTime: null,
};

// ── Scenario Definitions ─────────────────────────────────────
const SCENARIOS = {
  normal: {
    label: "Normal Agent",
    pid: () => 10000 + Math.floor(Math.random() * 5000),
    events: [
      { file: "/tmp/taxagent_sandbox_aX3f/test.txt",    write: false, delay: 800 },
      { file: "/tmp/taxagent_sandbox_aX3f/dummy_agent.py", write: false, delay: 1400 },
      { file: "/usr/lib/python3.10/abc.py",             write: false, delay: 1900 },
      { file: "/usr/lib/python3.10/io.py",              write: false, delay: 2200 },
      { file: "/tmp/taxagent_sandbox_aX3f/result.json", write: false, delay: 2600 },
    ],
  },
  curious: {
    label: "Curious Agent",
    pid: () => 20000 + Math.floor(Math.random() * 5000),
    events: [
      { file: "/tmp/taxagent_sandbox_bQ9z/test.txt",    write: false, delay: 600 },
      { file: "/proc/self/maps",                        write: false, delay: 1000, threat: true },
      { file: "/etc/hostname",                          write: false, delay: 1500, threat: true },
      { file: "/etc/os-release",                        write: false, delay: 1900, threat: true },
      { file: "/proc/net/tcp",                          write: false, delay: 2400, threat: true },
      { file: "/tmp/taxagent_sandbox_bQ9z/log.txt",     write: false, delay: 2800 },
    ],
  },
  malicious: {
    label: "Malicious Agent",
    pid: () => 30000 + Math.floor(Math.random() * 5000),
    events: [
      { file: "/tmp/taxagent_sandbox_cZ1w/test.txt",    write: false, delay: 500 },
      { file: "/etc/passwd",                            write: true,  delay: 900,  threat: true },
      { file: "/etc/shadow",                            write: true,  delay: 1300, threat: true },
      { file: "/root/.ssh/id_rsa",                      write: false, delay: 1700, threat: true },
      { file: "/etc/crontab",                           write: true,  delay: 2100, threat: true },
      { file: "/home/user/.bashrc",                     write: true,  delay: 2500, threat: true },
    ],
  },
};

// ── DOM References ────────────────────────────────────────────
const $ = id => document.getElementById(id);
const eventFeed = $("eventFeed");
const feedEmpty = $("feedEmpty");

// ── Utility ───────────────────────────────────────────────────
function randomBetween(a, b) { return a + Math.random() * (b - a); }
function fmtFlags(write) { return write ? "0x0241 (O_WRONLY|O_CREAT)" : "0x0000 (O_RDONLY)"; }
function fmtTimestamp() { return (performance.now() * 1e6 | 0).toString() + " ns"; }
function getCategory(file) {
  if (file.startsWith("/tmp"))  return "tmp";
  if (file.startsWith("/etc"))  return "etc";
  if (file.startsWith("/proc")) return "proc";
  if (file.startsWith("/home")) return "home";
  return "other";
}

// ── Radar Canvas ──────────────────────────────────────────────
const radarCtx = $("radarCanvas").getContext("2d");

function drawRadar(threatLevel) {
  // threatLevel: 0-1
  const canvas = $("radarCanvas");
  const W = canvas.width, H = canvas.height;
  const cx = W / 2, cy = H / 2, r = 90;
  radarCtx.clearRect(0, 0, W, H);

  // Grid rings
  [0.25, 0.5, 0.75, 1].forEach(scale => {
    radarCtx.beginPath();
    radarCtx.arc(cx, cy, r * scale, 0, Math.PI * 2);
    radarCtx.strokeStyle = "rgba(255,255,255,0.07)";
    radarCtx.lineWidth = 1;
    radarCtx.stroke();
  });

  // Axes (6 spokes)
  const labels = ["Files", "Write", "Sensitive", "Burst", "Network", "Entropy"];
  for (let i = 0; i < 6; i++) {
    const angle = (Math.PI * 2 * i) / 6 - Math.PI / 2;
    radarCtx.beginPath();
    radarCtx.moveTo(cx, cy);
    radarCtx.lineTo(cx + r * Math.cos(angle), cy + r * Math.sin(angle));
    radarCtx.strokeStyle = "rgba(255,255,255,0.07)";
    radarCtx.stroke();

    // Label
    const lx = cx + (r + 14) * Math.cos(angle);
    const ly = cy + (r + 14) * Math.sin(angle);
    radarCtx.fillStyle = "#475569";
    radarCtx.font = "9px Inter";
    radarCtx.textAlign = "center";
    radarCtx.textBaseline = "middle";
    radarCtx.fillText(labels[i], lx, ly);
  }

  // Data polygon — derive from state
  const total = Math.max(state.total, 1);
  const writeRatio = state.writes / total;
  const sensitiveRatio = (state.pathCounts.etc + state.pathCounts.proc + state.pathCounts.home) / total;
  const burstRate = Math.min(state.total / 10, 1);
  const entropyScore = Math.min(Object.values(state.pathCounts).filter(v => v > 0).length / 5, 1);

  const dataPoints = [
    Math.min(total / 10, 1),   // Files
    writeRatio,                // Write
    sensitiveRatio,            // Sensitive
    burstRate,                 // Burst
    0,                         // Network (future)
    entropyScore,              // Entropy
  ];

  radarCtx.beginPath();
  for (let i = 0; i < 6; i++) {
    const angle = (Math.PI * 2 * i) / 6 - Math.PI / 2;
    const val = dataPoints[i] * r;
    const x = cx + val * Math.cos(angle);
    const y = cy + val * Math.sin(angle);
    i === 0 ? radarCtx.moveTo(x, y) : radarCtx.lineTo(x, y);
  }
  radarCtx.closePath();

  const color = threatLevel > 0.6 ? "220,38,38" : threatLevel > 0.3 ? "217,119,6" : "5,150,105";
  radarCtx.fillStyle = `rgba(${color},0.15)`;
  radarCtx.strokeStyle = `rgba(${color},0.7)`;
  radarCtx.lineWidth = 2;
  radarCtx.fill();
  radarCtx.stroke();

  // Center dot
  radarCtx.beginPath();
  radarCtx.arc(cx, cy, 4, 0, Math.PI * 2);
  radarCtx.fillStyle = `rgba(${color},0.8)`;
  radarCtx.fill();
}

// ── Score Gauge (semi-circle arc) ─────────────────────────────
const gaugeCtx = $("scoreGauge").getContext("2d");

function drawGauge(score) {
  // score: 0-1
  const canvas = $("scoreGauge");
  const W = canvas.width, H = canvas.height;
  const cx = W / 2, cy = H - 10, r = 70;
  gaugeCtx.clearRect(0, 0, W, H);

  // Background arc
  gaugeCtx.beginPath();
  gaugeCtx.arc(cx, cy, r, Math.PI, 0, false);
  gaugeCtx.strokeStyle = "rgba(255,255,255,0.08)";
  gaugeCtx.lineWidth = 12;
  gaugeCtx.lineCap = "round";
  gaugeCtx.stroke();

  // Filled arc
  const endAngle = Math.PI + score * Math.PI;
  const color = score > 0.6 ? "#dc2626" : score > 0.3 ? "#d97706" : "#059669";
  gaugeCtx.beginPath();
  gaugeCtx.arc(cx, cy, r, Math.PI, endAngle, false);
  gaugeCtx.strokeStyle = color;
  gaugeCtx.lineWidth = 12;
  gaugeCtx.lineCap = "round";
  gaugeCtx.stroke();

  // Tip glow
  const tipAngle = endAngle;
  const tx = cx + r * Math.cos(tipAngle);
  const ty = cy + r * Math.sin(tipAngle);
  const grd = gaugeCtx.createRadialGradient(tx, ty, 0, tx, ty, 18);
  grd.addColorStop(0, color + "88");
  grd.addColorStop(1, color + "00");
  gaugeCtx.beginPath();
  gaugeCtx.arc(tx, ty, 18, 0, Math.PI * 2);
  gaugeCtx.fillStyle = grd;
  gaugeCtx.fill();
}

// ── Update Stats Display ──────────────────────────────────────
function updateStats() {
  $("statTotal").textContent = state.total;
  $("statWrites").textContent = state.writes;
  $("statReads").textContent = state.reads;

  // ML Risk Score (simulated Isolation Forest output)
  const total = Math.max(state.total, 1);
  const writeRatio = state.writes / total;
  const sensitiveHits = (state.pathCounts.etc + state.pathCounts.proc + state.pathCounts.home) / total;
  const riskScore = Math.min(writeRatio * 0.5 + sensitiveHits * 0.5, 1);
  const riskPct = Math.round(riskScore * 100);

  $("statRisk").textContent = riskPct + "%";
  drawGauge(riskScore);
  drawRadar(riskScore);
  $("mlScoreLabel").textContent = riskPct + "%";

  // ML Features
  const elapsed = state.startTime ? (Date.now() - state.startTime) / 1000 : 1;
  $("feat-entropy").textContent = (Math.log2(total + 1) / 5).toFixed(3);
  $("feat-ratio").textContent   = writeRatio.toFixed(3);
  $("feat-sensitive").textContent = (state.pathCounts.etc + state.pathCounts.proc + state.pathCounts.home);
  $("feat-burst").textContent   = (state.total / elapsed).toFixed(1) + " ev/s";

  const decision = riskScore > 0.6 ? "ANOMALY"
                 : riskScore > 0.3 ? "SUSPICIOUS"
                 :                   "NORMAL";

  const decisionEl = $("feat-decision");
  decisionEl.style.color = riskScore > 0.6 ? "var(--red)" : riskScore > 0.3 ? "var(--amber)" : "var(--green)";
  $("feat-decision").textContent = decision;

  // Bar chart
  const cats = state.pathCounts;
  const catTotal = Math.max(Object.values(cats).reduce((a, b) => a + b, 0), 1);
  Object.entries(cats).forEach(([key, val]) => {
    const pct = Math.round((val / catTotal) * 100);
    $(`bar-${key}`).style.width = pct + "%";
    $(`pct-${key}`).textContent = pct + "%";
  });

  // Kernel info
  $("ki-pids").textContent = state.agentPid ? "1 entry" : "0 entries";
  $("ki-pid").textContent  = state.agentPid || "none";
}

// ── Render Event Card ─────────────────────────────────────────
function renderEvent(ev, prepend = false) {
  if (state.filterMode === "write"  && !ev.write)   return;
  if (state.filterMode === "threat" && !ev.threat)  return;

  feedEmpty.style.display = "none";

  const card = document.createElement("div");
  card.className = `event-card ${ev.write ? "write" : "read"}`;
  card.dataset.write  = ev.write ? "1" : "0";
  card.dataset.threat = ev.threat ? "1" : "0";

  const tagClass = ev.threat ? "threat" : ev.write ? "write" : "read";
  const tagLabel = ev.threat ? "THREAT" : ev.write ? "WRITE" : "READ";

  card.innerHTML = `
    <div class="event-body">
      <div class="event-file">${ev.file}</div>
      <div class="event-meta">
        <span>pid ${ev.pid}</span>
        <span>cpu ${Math.floor(Math.random() * 8)}</span>
        <span>${ev.flags}</span>
        <span>${ev.ts}</span>
      </div>
    </div>
    <span class="event-tag ${tagClass}">${tagLabel}</span>
  `;

  if (prepend) {
    eventFeed.insertBefore(card, eventFeed.firstChild);
  } else {
    eventFeed.appendChild(card);
  }

  // Auto-scroll to latest
  eventFeed.scrollTop = 0;
}

// ── Re-render Feed (after filter change) ─────────────────────
function reRenderFeed() {
  eventFeed.innerHTML = "";
  const filtered = state.events.filter(ev => {
    if (state.filterMode === "write")  return ev.write;
    if (state.filterMode === "threat") return ev.threat;
    return true;
  });
  if (filtered.length === 0) {
    eventFeed.appendChild(feedEmpty);
    feedEmpty.style.display = "flex";
  } else {
    filtered.slice().reverse().forEach(ev => renderEvent(ev, false));
  }
}

// ── Add Event to State ────────────────────────────────────────
function addEvent(ev) {
  state.total++;
  if (ev.write) state.writes++; else state.reads++;
  const cat = getCategory(ev.file);
  state.pathCounts[cat]++;

  const fullEv = {
    ...ev,
    pid:   state.agentPid,
    flags: fmtFlags(ev.write),
    ts:    fmtTimestamp(),
    cat,
  };
  state.events.unshift(fullEv);
  renderEvent(fullEv, true);
  updateStats();
}

// ── Run Scenario ──────────────────────────────────────────────
function runScenario(name) {
  if (state.running) return;
  const scenario = SCENARIOS[name];
  if (!scenario) return;

  state.running  = true;
  state.scenario = name;
  state.agentPid = scenario.pid();
  state.startTime = Date.now();
  $("ki-pid").textContent = state.agentPid;
  $("ki-pids").textContent = "1 entry";
  $("statusText").textContent = "LIVE — PID " + state.agentPid;

  scenario.events.forEach(({ file, write, delay, threat = false }) => {
    setTimeout(() => {
      addEvent({ file, write, threat });

      // Check if last event
      const last = scenario.events[scenario.events.length - 1];
      if (file === last.file) {
        setTimeout(() => {
          state.running  = false;
          state.agentPid = null;
          $("statusText").textContent = "SIMULATED LIVE";
          $("ki-pid").textContent  = "none";
          $("ki-pids").textContent = "0 entries";
        }, 1000);
      }
    }, delay);
  });
}

// ── Modal ─────────────────────────────────────────────────────
const overlay = $("modalOverlay");

$("btnRunAgent").addEventListener("click", () => {
  if (!state.running) overlay.classList.add("open");
});

$("modalClose").addEventListener("click", () => overlay.classList.remove("open"));
overlay.addEventListener("click", e => { if (e.target === overlay) overlay.classList.remove("open"); });

document.querySelectorAll(".scenario-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    overlay.classList.remove("open");
    runScenario(btn.dataset.scenario);
  });
});

// ── Filter Buttons ────────────────────────────────────────────
document.querySelectorAll(".filter-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    state.filterMode = btn.dataset.filter;
    reRenderFeed();
  });
});

// ── Clear Log ─────────────────────────────────────────────────
$("btnClearLog").addEventListener("click", () => {
  Object.assign(state, {
    total: 0, writes: 0, reads: 0,
    events: [],
    pathCounts: { tmp: 0, etc: 0, proc: 0, home: 0, other: 0 },
  });
  eventFeed.innerHTML = "";
  eventFeed.appendChild(feedEmpty);
  feedEmpty.style.display = "flex";
  updateStats();
  drawGauge(0);
  drawRadar(0);
});

// ── Init ──────────────────────────────────────────────────────
drawGauge(0);
drawRadar(0);
updateStats();

// ── Live eBPF Integration (SSE Client) ────────────────────────
if (window.location.protocol.startsWith("http")) {
  console.log("Connecting to live eBPF stream at /stream...");
  const sse = new EventSource("/stream");
  
  sse.addEventListener("spawn", (e) => {
    const data = JSON.parse(e.data);
    state.running = true;
    state.agentPid = data.pid;
    state.startTime = Date.now();
    $("ki-pid").textContent = state.agentPid;
    $("ki-pids").textContent = "1 entry";
    $("statusText").textContent = "LIVE — PID " + state.agentPid;
    // Reset state for new run
    state.total = 0; state.writes = 0; state.reads = 0;
    state.events = [];
    state.pathCounts = { tmp: 0, etc: 0, proc: 0, home: 0, other: 0 };
    eventFeed.innerHTML = "";
    eventFeed.appendChild(feedEmpty);
    feedEmpty.style.display = "flex";
    updateStats();
  });
  
  sse.addEventListener("syscall", (e) => {
    const data = JSON.parse(e.data);
    const p = data.filename;
    const isThreat = p.startsWith("/etc") || p.startsWith("/proc") || p.startsWith("/home") || p.startsWith("/root");
    addEvent({
      file: data.filename,
      write: data.is_write,
      threat: isThreat
    });
  });
  
  sse.addEventListener("exit", (e) => {
    const data = JSON.parse(e.data);
    state.running = false;
    state.agentPid = null;
    $("statusText").textContent = `STOPPED — Code ${data.code}`;
    $("ki-pid").textContent = "none";
    $("ki-pids").textContent = "0 entries";
  });
}


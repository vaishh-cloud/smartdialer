from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

from app.database import Base, engine
from app.api.routes import router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB schema on startup
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(
    title="CredResolve SmartDialer",
    description="Backend-first SmartDialer with validated state machines, atomic allocation, and independent safety controller.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>CredResolve SmartDialer Console</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: #111827;
      --border: #1f2937;
      --accent: #3b82f6;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); padding: 24px; }
    header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px; }
    h1 { font-size: 20px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
    .badge { font-family: 'JetBrains Mono', monospace; font-size: 11px; padding: 4px 8px; border-radius: 4px; background: rgba(59,130,246,0.2); color: var(--accent); border: 1px solid var(--accent); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 24px; }
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 18px; }
    .card-title { font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 10px; }
    .card-val { font-size: 28px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
    .controls { display: flex; gap: 10px; align-items: center; margin-bottom: 24px; flex-wrap: wrap; }
    button { background: var(--accent); color: white; border: none; border-radius: 6px; padding: 10px 16px; font-weight: 600; cursor: pointer; font-size: 13px; transition: opacity 0.2s; }
    button:hover { opacity: 0.9; }
    button.secondary { background: #374151; }
    button.warning { background: var(--warning); color: #111; }
    .table-container { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 24px; }
    table { width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }
    th { background: #1a2234; padding: 12px 16px; font-weight: 600; color: var(--text-muted); border-bottom: 1px solid var(--border); font-size: 12px; }
    td { padding: 12px 16px; border-bottom: 1px solid var(--border); }
    tr:last-child td { border-bottom: none; }
    .status-pill { font-family: 'JetBrains Mono', monospace; font-size: 11px; padding: 3px 8px; border-radius: 9999px; font-weight: 600; }
    .st-AVAILABLE { background: rgba(16,185,129,0.15); color: var(--success); }
    .st-DIALING { background: rgba(245,158,11,0.15); color: var(--warning); }
    .st-CONNECTED { background: rgba(59,130,246,0.2); color: var(--accent); }
    .st-WRAP_UP { background: rgba(168,85,247,0.2); color: #c084fc; }
    .st-OFFLINE { background: rgba(107,114,128,0.2); color: #9ca3af; }
    .log-terminal { background: #000; border: 1px solid var(--border); border-radius: 8px; padding: 14px; font-family: 'JetBrains Mono', monospace; font-size: 12px; color: #10b981; max-height: 200px; overflow-y: auto; }
  </style>
</head>
<body>
  <header>
    <h1>CredResolve SmartDialer <span class="badge">TECHNICAL PROTOTYPE</span></h1>
    <div id="health-badge" class="badge">HEALTHY</div>
  </header>

  <div class="controls">
    <button onclick="triggerDialCycle()">Run Dial Cycle</button>
    <button class="secondary" onclick="toggleMode()">Toggle Mode (Progressive / Predictive)</button>
    <button class="secondary" onclick="seedData()">Seed Test Data</button>
    <button class="warning" onclick="reconcile()">Run Reconciler</button>
    <span id="current-mode" style="font-weight: 600; margin-left: 10px; color: #60a5fa;">Mode: ...</span>
  </div>

  <div class="grid">
    <div class="card">
      <div class="card-title">Available Agents</div>
      <div class="card-val" id="avail-agents" style="color: var(--success);">-</div>
    </div>
    <div class="card">
      <div class="card-title">Busy / In-Call Agents</div>
      <div class="card-val" id="busy-agents" style="color: var(--warning);">-</div>
    </div>
    <div class="card">
      <div class="card-title">In-Flight Calls</div>
      <div class="card-val" id="inflight-calls" style="color: var(--accent);">-</div>
    </div>
    <div class="card">
      <div class="card-title">Completed Calls</div>
      <div class="card-val" id="completed-calls" style="color: #a78bfa;">-</div>
    </div>
  </div>

  <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
    <div>
      <h3 style="font-size: 14px; margin-bottom: 8px;">Live Agents</h3>
      <div class="table-container">
        <table>
          <thead>
            <tr><th>ID</th><th>Name</th><th>Status</th><th>Handled</th></tr>
          </thead>
          <tbody id="agents-tbody"><tr><td colspan="4">Loading agents...</td></tr></tbody>
        </table>
      </div>
    </div>

    <div>
      <h3 style="font-size: 14px; margin-bottom: 8px;">Recent Calls</h3>
      <div class="table-container">
        <table>
          <thead>
            <tr><th>ID</th><th>Borrower</th><th>Agent</th><th>Status</th><th>Provider</th></tr>
          </thead>
          <tbody id="calls-tbody"><tr><td colspan="5">Loading calls...</td></tr></tbody>
        </table>
      </div>
    </div>
  </div>

  <h3 style="font-size: 14px; margin-bottom: 8px;">Live Safety Controller & System Audit Log</h3>
  <div class="log-terminal" id="terminal-log">
    [System] SmartDialer initialized. Ready for operations.
  </div>

  <script>
    let activeCampaignId = 1;
    let currentMode = "PROGRESSIVE";

    function log(msg) {
      const term = document.getElementById('terminal-log');
      const time = new Date().toLocaleTimeString();
      term.innerHTML = `[${time}] ${msg}<br>` + term.innerHTML;
    }

    async function loadData() {
      try {
        const statsRes = await fetch('/api/stats');
        const stats = await statsRes.json();
        document.getElementById('avail-agents').innerText = stats.agents.available;
        document.getElementById('busy-agents').innerText = stats.agents.busy;
        document.getElementById('inflight-calls').innerText = stats.calls.in_flight;
        document.getElementById('completed-calls').innerText = stats.calls.completed;
        document.getElementById('health-badge').innerText = stats.circuit_breaker.state;

        const campsRes = await fetch('/api/campaigns');
        const camps = await campsRes.json();
        if (camps.length > 0) {
          activeCampaignId = camps[0].id;
          currentMode = camps[0].mode;
          document.getElementById('current-mode').innerText = `Campaign Mode: ${currentMode}`;
        }

        const agentsRes = await fetch('/api/agents');
        const agents = await agentsRes.json();
        const aTbody = document.getElementById('agents-tbody');
        aTbody.innerHTML = agents.map(a => `
          <tr>
            <td>${a.id}</td>
            <td>${a.name}</td>
            <td><span class="status-pill st-${a.status}">${a.status}</span></td>
            <td>${a.total_calls_handled}</td>
          </tr>
        `).join('') || '<tr><td colspan="4">No agents. Click "Seed Test Data".</td></tr>';

        const callsRes = await fetch('/api/calls?limit=8');
        const calls = await callsRes.json();
        const cTbody = document.getElementById('calls-tbody');
        cTbody.innerHTML = calls.map(c => `
          <tr>
            <td>#${c.id}</td>
            <td>${c.borrower_id}</td>
            <td>${c.agent_id ? '#' + c.agent_id : 'Auto'}</td>
            <td><span class="status-pill st-${c.status}">${c.status}</span></td>
            <td>${c.provider_name || 'Pending'}</td>
          </tr>
        `).join('') || '<tr><td colspan="5">No calls placed yet.</td></tr>';

      } catch (err) {
        console.error(err);
      }
    }

    async function triggerDialCycle() {
      log(`Triggering Dial Cycle for Campaign #${activeCampaignId}...`);
      const res = await fetch(`/api/campaigns/${activeCampaignId}/dial-cycle`, { method: 'POST' });
      const data = await res.json();
      log(`Safety Decision: ${data.safety_decision} | Proposed: ${data.proposed_calls} | Allowed: ${data.allowed_calls} | Placed: ${data.calls_placed}`);
      loadData();
    }

    async function toggleMode() {
      const nextMode = currentMode === "PROGRESSIVE" ? "PREDICTIVE" : "PROGRESSIVE";
      await fetch(`/api/campaigns/${activeCampaignId}/mode?mode=${nextMode}`, { method: 'POST' });
      log(`Switched Campaign mode to ${nextMode}`);
      loadData();
    }

    async function seedData() {
      log('Seeding demo agents, borrowers, and campaign...');
      await fetch('/api/seed-demo', { method: 'POST' });
      log('Demo data seeded successfully.');
      loadData();
    }

    async function reconcile() {
      log('Running state reconciler...');
      const res = await fetch('/api/reconcile', { method: 'POST' });
      const data = await res.json();
      log(`Reconciler finished: Cleared ${data.stale_reservations_cleared} stale reservations, failed ${data.stale_initiated_calls_failed} hanging calls.`);
      loadData();
    }

    setInterval(loadData, 3000);
    loadData();
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return DASHBOARD_HTML

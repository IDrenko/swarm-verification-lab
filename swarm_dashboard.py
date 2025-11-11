#!/usr/bin/env python3
from flask import Flask, render_template_string, Response, request
import sqlite3, time, os

DB = os.getenv("DB", os.path.expanduser("~/swarm_net.db"))
TZ = os.getenv("TZ", "Europe/London")  # for display; uses browser tz actually with JS
ONLINE_THRESHOLD_SEC = int(os.getenv("ONLINE_THRESHOLD_SEC", "120"))

app = Flask(__name__)

PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Swarm Dashboard</title>
  <style>
    body { font: 14px/1.3 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px; }
    h1 { margin: 0 0 8px; }
    .meta { color:#666; margin-bottom:16px; }
    table { border-collapse: collapse; width: 100%; margin: 18px 0; }
    th, td { border: 1px solid #ddd; padding: 8px; }
    th { background: #f3f3f3; position: sticky; top:0; }
    .ok { color: #0a7f2e; font-weight: 600; }
    .bad { color: #b00020; font-weight: 600; }
    .toolbar a, .toolbar button { display:inline-block; margin-right:10px; padding:6px 10px; border:1px solid #ccc; background:#fafafa; text-decoration:none; color:#000; border-radius:6px; }
    .toolbar { margin: 6px 0 18px; }
    @media print { .toolbar { display:none; } th { background:#eee !important; } }
  </style>
</head>
<body>
  <h1>Swarm Dashboard</h1>
  <div class="meta">Database: {{ db_path }} · Now: <span id="now"></span></div>
  <div class="toolbar">
    <a href="/devices.csv" download>Download devices.csv</a>
    <a href="/detections.csv" download>Download detections.csv</a>
    <button onclick="window.print()">Print / Save as PDF</button>
  </div>

  <h2>Devices</h2>
  <table>
    <tr>
      <th>MAC</th><th>Last IP</th><th>First Seen</th><th>Last Seen</th><th>Status</th><th>Seen Count</th>
    </tr>
    {% for d in devices %}
    <tr>
      <td>{{ d['mac'] }}</td>
      <td>{{ d['last_ip'] or '' }}</td>
      <td data-ts="{{ d['first_seen_ts'] }}">{{ d['first_seen_human'] }}</td>
      <td data-ts="{{ d['last_seen_ts'] }}">{{ d['last_seen_human'] }}</td>
      <td>
        {% if d['online'] %}
          <span class="ok">Online</span>
        {% else %}
          <span class="bad">Offline</span>
        {% endif %}
      </td>
      <td>{{ d['seen_count'] }}</td>
    </tr>
    {% endfor %}
  </table>

  <h2>Recent Detections</h2>
  <table>
    <tr>
      <th>Time</th><th>Robot</th><th>Event</th><th>MAC</th><th>IP</th><th>Confidence</th>
    </tr>
    {% for r in detections %}
    <tr>
      <td data-ts="{{ r['ts_ms'] }}">{{ r['time_human'] }}</td>
      <td>{{ r['robot_id'] }}</td>
      <td>{{ r['event_type'] }}</td>
      <td>{{ r['mac'] or '' }}</td>
      <td>{{ r['ip'] or '' }}</td>
      <td>{{ "%.2f"|format(r['confidence'] or 0) }}</td>
    </tr>
    {% endfor %}
  </table>

<script>
// replace server-provided human times with browser-local formatting
for (const el of document.querySelectorAll('[data-ts]')) {
  const ms = parseInt(el.getAttribute('data-ts'), 10);
  if (!isNaN(ms)) {
    const d = new Date(ms);
    el.textContent = d.toLocaleString();
  }
}
document.getElementById('now').textContent = new Date().toLocaleString();
</script>
</body>
</html>
"""

def q(sql, args=()):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(sql, args)
    rows = cur.fetchall()
    con.close()
    return rows

def human(ts_ms):  # server-side fallback
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_ms/1000)) if ts_ms else ""

@app.route("/")
def home():
    # Devices with online/offline
    now = int(time.time())
    devices = q("""
      SELECT mac, first_seen_ts, last_seen_ts, last_ip, seen_count
      FROM devices
      ORDER BY last_seen_ts DESC
      LIMIT 500
    """)
    devs = []
    for d in devices:
      last_seen = (d["last_seen_ts"] or 0)//1000
      online = (now - last_seen) <= ONLINE_THRESHOLD_SEC if last_seen else False
      devs.append({
        "mac": d["mac"],
        "first_seen_ts": d["first_seen_ts"],
        "last_seen_ts": d["last_seen_ts"],
        "last_ip": d["last_ip"],
        "seen_count": d["seen_count"],
        "online": online,
        "first_seen_human": human(d["first_seen_ts"]),
        "last_seen_human": human(d["last_seen_ts"]),
      })

    detections = q("""
      SELECT ts_ms, robot_id, event_type, mac, ip, confidence
      FROM detections
      ORDER BY id DESC
      LIMIT 500
    """)
    dets = []
    for r in detections:
      dets.append({
        "ts_ms": r["ts_ms"],
        "time_human": human(r["ts_ms"]),
        "robot_id": r["robot_id"],
        "event_type": r["event_type"],
        "mac": r["mac"],
        "ip": r["ip"],
        "confidence": r["confidence"],
      })

    return render_template_string(PAGE, devices=devs, detections=dets, db_path=DB)

@app.route("/devices.csv")
def devices_csv():
    rows = q("SELECT mac, first_seen_ts, last_seen_ts, last_ip, seen_count FROM devices ORDER BY last_seen_ts DESC")
    def gen():
        yield "mac,first_seen_ts,last_seen_ts,first_seen,last_seen,last_ip,seen_count,online\n"
        now = int(time.time())
        for r in rows:
            online = int((now - (r['last_seen_ts']//1000)) <= ONLINE_THRESHOLD_SEC) if r['last_seen_ts'] else 0
            yield f"{r['mac']},{r['first_seen_ts']},{r['last_seen_ts']}," \
                  f"\"{human(r['first_seen_ts'])}\",\"{human(r['last_seen_ts'])}\"," \
                  f"{r['last_ip'] or ''},{r['seen_count']},{online}\n"
    return Response(gen(), mimetype="text/csv")

@app.route("/detections.csv")
def detections_csv():
    rows = q("SELECT ts_ms, robot_id, event_type, mac, ip, confidence FROM detections ORDER BY id DESC")
    def gen():
        yield "ts_ms,time,robot_id,event_type,mac,ip,confidence\n"
        for r in rows:
            yield f"{r['ts_ms']},\"{human(r['ts_ms'])}\",{r['robot_id']}," \
                  f"{r['event_type']},{r['mac'] or ''},{r['ip'] or ''},{r['confidence'] or 0}\n"
    return Response(gen(), mimetype="text/csv")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8088, debug=False)

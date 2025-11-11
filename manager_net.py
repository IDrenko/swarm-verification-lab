#!/usr/bin/env python3
import json, sqlite3, time, os
import paho.mqtt.client as mqtt

BROKER = os.getenv("BROKER", "127.0.0.1")
DB = os.getenv("DB", os.path.expanduser("~/swarm_net.db"))

ONLINE_THRESHOLD_SEC = 120  # consider a device "online" if seen within this window

conn = sqlite3.connect(DB, check_same_thread=False)
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS detections(
  id INTEGER PRIMARY KEY,
  ts_ms INTEGER,
  robot_id TEXT,
  event_type TEXT,
  mac TEXT,
  ip TEXT,
  confidence REAL,
  task_id TEXT,
  raw_json TEXT
)""")
cur.execute("""CREATE TABLE IF NOT EXISTS telemetry(
  id INTEGER PRIMARY KEY,
  ts_ms INTEGER,
  robot_id TEXT,
  type TEXT,
  raw_json TEXT
)""")
cur.execute("""CREATE TABLE IF NOT EXISTS devices(
  mac TEXT PRIMARY KEY,
  first_seen_ts INTEGER,
  last_seen_ts INTEGER,
  last_ip TEXT,
  seen_count INTEGER
)""")
conn.commit()

def now_ms(): return int(time.time()*1000)

def upsert_device(mac: str, ip: str, ts_ms: int):
    row = cur.execute("SELECT mac, first_seen_ts, last_seen_ts, last_ip, seen_count FROM devices WHERE mac=?",(mac,)).fetchone()
    if row is None:
        cur.execute("INSERT INTO devices(mac, first_seen_ts, last_seen_ts, last_ip, seen_count) VALUES(?,?,?,?,?)",
                    (mac, ts_ms, ts_ms, ip, 1))
    else:
        _, first_seen, _, _, seen_count = row
        cur.execute("UPDATE devices SET last_seen_ts=?, last_ip=?, seen_count=? WHERE mac=?",
                    (ts_ms, ip, seen_count+1, mac))
    conn.commit()

def on_message(client, userdata, msg):
    try:
        d = json.loads(msg.payload.decode())
    except Exception:
        return

    # Detections
    if d.get("type") == "DETECTION":
        feats = d.get("features", {})
        ts = int(d.get("ts", now_ms()))
        mac = feats.get("mac")
        ip  = feats.get("ip")

        cur.execute("""INSERT INTO detections(ts_ms,robot_id,event_type,mac,ip,confidence,task_id,raw_json)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (ts,
                     d.get("robot_id"),
                     feats.get("event_type"),
                     mac, ip,
                     float(d.get("confidence", 0)),
                     d.get("task_id"),
                     json.dumps(d)))
        conn.commit()

        # Maintain presence on NEW_DEVICE/IP_CHANGED and any event that includes a MAC/IP
        if mac and ip:
            upsert_device(mac, ip, ts)

        # You can extend here to mark a device offline if you ever emit DEVICE_GONE,
        # otherwise "offline" will be computed at query-time from last_seen_ts.

    # Telemetry (heartbeat, etc.)
    elif d.get("type") in ("HEARTBEAT","EVENT","ERROR","ACK"):
        cur.execute("""INSERT INTO telemetry(ts_ms,robot_id,type,raw_json)
                       VALUES(?,?,?,?)""",
                    (int(d.get("ts", now_ms())),
                     d.get("robot_id"),
                     d.get("type"),
                     json.dumps(d)))
        conn.commit()

client = mqtt.Client("manager_net")
client.on_message = on_message
client.connect(BROKER)
client.loop_start()
client.subscribe("swarm/#")
print("Manager running. Subscribed to swarm/#. Writing to", DB)

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass

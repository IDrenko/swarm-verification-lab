#!/usr/bin/env python3
"""
Robot Network Monitor Agent — MQTT v1/v2 robust + local logging

- Scans LAN neighbors via /proc/net/arp + `ip neigh` (no root needed)
- Publishes NEW_DEVICE / IP_CHANGED / MAC_CHANGED / DEVICE_GONE
- Retries MQTT connection forever; never crashes the service
- Logs everything to /var/log/robot_agent.log (and stdout/journalctl)
"""

import time, json, socket, subprocess, sys, os
import logging

# ---------- CONFIG ----------
BROKER_IP = "M1.local"     # replace with M1's IP if mDNS doesn't work
SCAN_INTERVAL_S = 8
DEPARTURE_TIMEOUT_S = 90
LOG_PATH = "/var/log/robot_agent.log"
# ----------------------------

# -------- Local Logging Setup --------
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
logging.getLogger().addHandler(_console)

def log(msg):
    print(msg, flush=True)         # visible in `journalctl -u robot-net-agent`
    logging.info(msg)

# -------- MQTT (v1/v2 compatible) --------
try:
    import paho.mqtt.client as mqtt
except Exception:
    log("ERROR: paho-mqtt missing. Install with: sudo apt install -y python3-paho-mqtt")
    raise

ROBOT_ID = socket.gethostname()
TEL = f"swarm/telemetry/{ROBOT_ID}"

def now_ms(): return int(time.time() * 1000)

def _build_client():
    """Create an MQTT client that tolerates both paho v1 and v2."""
    proto = getattr(mqtt, "MQTTv311", 4)

    # Try v2 style first (with callback_api_version). If rejected, fall back to v1.
    try:
        capi = getattr(mqtt, "CallbackAPIVersion", None)
        if capi is not None:
            try:
                return mqtt.Client(
                    client_id=ROBOT_ID,
                    protocol=proto,
                    callback_api_version=capi.v5  # v2 path
                )
            except Exception:
                pass  # fall through to v1 style
        return mqtt.Client(client_id=ROBOT_ID, protocol=proto)
    except (TypeError, ValueError):
        return mqtt.Client(client_id=ROBOT_ID, protocol=proto)

def _connect_with_retry():
    c = _build_client()
    while True:
        try:
            c.connect(BROKER_IP)
            c.loop_start()
            log(f"[INFO] Connected to MQTT broker at {BROKER_IP}")
            # best-effort hello
            try:
                c.publish(TEL, json.dumps({"type":"HEARTBEAT","robot_id":ROBOT_ID,"ts":now_ms(),"msg":"connected"}))
            except Exception:
                pass
            return c
        except Exception as e:
            log(f"[WARN] MQTT connect failed ({e}); retrying in 5s")
            time.sleep(5)

client = _connect_with_retry()

def pub(topic, obj):
    """Publish with one-shot reconnect on failure."""
    global client
    payload = json.dumps(obj)
    try:
        client.publish(topic, payload)
    except Exception as e:
        log(f"[WARN] publish failed ({e}); reconnecting…")
        try:
            client.loop_stop()
        except Exception:
            pass
        client = _connect_with_retry()
        client.publish(topic, payload)

# -------- Neighbor scanning (no root required) --------
def scan_proc_arp():
    """Return {mac: ip} discovered from kernel neighbor tables."""
    results = {}

    # /proc/net/arp
    try:
        with open("/proc/net/arp","r") as f:
            lines = f.readlines()[1:]
        for line in lines:
            parts = line.split()
            if len(parts) >= 4:
                ip = parts[0]
                mac = parts[3].lower()
                if mac and mac != "00:00:00:00:00:00":
                    results[mac] = ip
    except Exception:
        pass

    # `ip neigh`
    try:
        out = subprocess.check_output(["ip","neigh"], stderr=subprocess.DEVNULL).decode()
        for line in out.splitlines():
            p = line.split()
            if len(p) >= 5:
                ip = p[0]
                mac = p[4].lower() if p[4].count(':') == 5 else None
                if mac and mac != "00:00:00:00:00:00":
                    results[mac] = ip
    except Exception:
        pass

    return results

# -------------- Event logic --------------
known = {}  # mac -> {ip, first_seen, last_seen, seen_count, gone?}

def publish_event(event_type, details, confidence=None):
    task_id = f"NET-{time.strftime('%Y%m%d')}"
    topic = f"swarm/detections/{task_id}/{ROBOT_ID}"

    if confidence is None:
        confidence = {
            "NEW_DEVICE": 0.95,
            "IP_CHANGED":  0.90,
            "MAC_CHANGED": 0.90,
            "DEVICE_GONE": 0.70,
        }.get(event_type, 0.60)

    log(f"[EVENT] {event_type}: {details}")

    payload = {
        "type": "DETECTION",
        "task_id": task_id,
        "round": 1,
        "robot_id": ROBOT_ID,
        "ts": now_ms(),
        "confidence": confidence,
        "features": {"event_type": event_type, **details},
    }
    pub(topic, payload)
    pub(TEL, {"type":"EVENT","robot_id":ROBOT_ID,"ts":now_ms(),"event":event_type,"summary":details})

def do_scan_and_diff():
    ts = time.time()
    current = scan_proc_arp()  # {mac: ip}

    # NEW_DEVICE / IP_CHANGED
    for mac, ip in current.items():
        if mac not in known:
            known[mac] = {"ip": ip, "first_seen": ts, "last_seen": ts, "seen_count": 1}
            publish_event("NEW_DEVICE", {"mac": mac, "ip": ip})
        else:
            rec = known[mac]
            if rec.get("ip") != ip:
                prev_ip = rec["ip"]
                rec["ip"] = ip
                rec["last_seen"] = ts
                rec["seen_count"] = rec.get("seen_count", 0) + 1
                publish_event("IP_CHANGED", {"mac": mac, "prev_ip": prev_ip, "ip": ip})
            else:
                rec["last_seen"] = ts
                rec["seen_count"] = rec.get("seen_count", 0) + 1
                rec.pop("gone", None)

    # MAC_CHANGED (same IP mapped to different MAC in this snapshot)
    ip_to_mac = {ip: mac for mac, ip in current.items()}
    for mac, rec in list(known.items()):
        prev_ip = rec.get("ip")
        if not prev_ip:
            continue
        new_mac = ip_to_mac.get(prev_ip)
        if new_mac and new_mac != mac:
            publish_event("MAC_CHANGED", {"ip": prev_ip, "prev_mac": mac, "mac": new_mac})
            if new_mac not in known:
                known[new_mac] = {"ip": prev_ip, "first_seen": ts, "last_seen": ts, "seen_count": 1}

    # DEVICE_GONE
    cutoff = ts - DEPARTURE_TIMEOUT_S
    for mac, rec in list(known.items()):
        if rec.get("last_seen", 0) < cutoff and not rec.get("gone"):
            rec["gone"] = True
            publish_event("DEVICE_GONE", {"mac": mac, "ip": rec.get("ip"), "last_seen_ms": int(rec.get("last_seen",0)*1000)})

def main():
    log("[START] robot_net_agent starting")
    pub(TEL, {"type":"HEARTBEAT","robot_id":ROBOT_ID,"ts":now_ms(),"msg":"agent-start"})
    while True:
        try:
            do_scan_and_diff()
            time.sleep(SCAN_INTERVAL_S)
        except Exception as e:
            log(f"[ERROR] {e}")
            pub(TEL, {"type":"ERROR","robot_id":ROBOT_ID,"ts":now_ms(),"error":str(e)[:200]})
            time.sleep(2)

if __name__ == "__main__":
    main()

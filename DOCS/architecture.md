
---

### docs/architecture.md
```markdown
# 🧠 Architecture Details

This document explains the system layout, message flow, and verification process.

##
[ R1...Rn (Robots) ] → MQTT → [ M1 Manager ] → SQLite → Dashboard
│
└──> ROS 2 Bridge → /swarm/detection

## Components

- **R1...Rn:** Raspberry Pi agents (Python scripts + systemd services)
- **M1:** Manager host (MQTT broker, SQLite, dashboard)
- **ROS 2 Bridge:** Connects MQTT messages into ROS 2 topics for simulation/analysis

## Message Flow

1. Robots publish detections to MQTT (`swarm/detections/#`)
2. Manager subscribes and logs events into `swarm_net.db`
3. Dashboard visualizes detections and device states
4. ROS 2 node republishes data on `/swarm/detection`



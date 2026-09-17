"""AdaptiveSense data collector.

Subscribes to the node's MQTT topic and append each payload to a CSV log. This
is the receiving side of the system and is used when a real node streams data.

Dependencies (optional at runtime): paho-mqtt

    pip install paho-mqtt

Usage:

    python server/mqtt_collector.py --host localhost --port 1883 \
        --topic adaptivesense/data --out results/live/MQTT-net.csv

All configuration is command-line only; nothing sensitive is hard-coded.
"""

from __future__ import annotations

import argparse
import csv
import json
import signal
import sys
from pathlib import Path


FIELDS = [
    "device_id", "timestamp", "temperature", "humidity", "pressure", "light",
    "sampling_interval", "state", "event", "valid",
]


class Collector:
    def __init__(self, topic: str, out_path: Path):
        self.topic = topic
        self.out_path = out_path
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.csvfile = open(out_path, "a", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.csvfile, fieldnames=FIELDS)
        # write header once if the file is new/empty
        if self.out_path.stat().st_size == 0:
            self.writer.writeheader()
            self.csvfile.flush()

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        print(f"[collector] connected, subscribing to '{self.topic}'", flush=True)
        client.subscribe(self.topic)

    def on_message(self, client, userdata, msg):
        try:
            rec = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            print(f"[collector] invalid JSON: {exc}", file=sys.stderr, flush=True)
            return
        # keep only known fields, in a stable order; nested values (the per-channel
        # `valid` map) are kept as compact JSON rather than dropped, so a reader can
        # still tell "the sensor reported 0" from "that channel has no sensor"
        row = {}
        for f in FIELDS:
            value = rec.get(f, "")
            if isinstance(value, (dict, list)):
                value = json.dumps(value, separators=(",", ":"), sort_keys=True)
            row[f] = value
        self.writer.writerow(row)
        self.csvfile.flush()
        print(f"[collector] {msg.topic}: {msg.payload.decode()}", flush=True)

    def close(self):
        self.csvfile.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--topic", default="adaptivesense/data")
    ap.add_argument("--out", default="results/live/node_log.csv")
    args = ap.parse_args()

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("paho-mqtt not installed. Install it with: pip install paho-mqtt",
              file=sys.stderr)
        return 2

    collector = Collector(args.topic, Path(args.out))
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = collector.on_connect
    client.on_message = collector.on_message
    client.connect(args.host, args.port, 60)
    client.loop_start()

    def stop(*_):
        collector.close()
        client.loop_stop()
        print("[collector] bye", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print(f"[collector] listening on {args.host}:{args.port} topic={args.topic} "
          f"-> {args.out}", flush=True)

    # keep the process alive; handled via signal handlers
    while True:
        import time
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
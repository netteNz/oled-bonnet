#!/usr/bin/env python3
"""Pull live telemetry from a Prometheus server: CPU temp, load, usage %.

Standalone script, not wired into the HUD/compositor/daemon — just a way to
look at what real values would eventually feed the HUD.

    .env/bin/python3 scripts/prom_pull.py
    .env/bin/python3 scripts/prom_pull.py --url http://192.168.50.249:9090
    .env/bin/python3 scripts/prom_pull.py --interval 2
"""
import argparse
import json
import time
import urllib.parse
import urllib.request

QUERIES = {
    "cpu_temp_c": 'node_thermal_zone_temp{type="cpu-thermal"}',
    "load1": "node_load1",
    "load5": "node_load5",
    "load15": "node_load15",
    "cpu_usage_pct": '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)',
}


def query(base_url: str, promql: str, timeout: float = 4.0) -> float | None:
    url = base_url.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": promql})
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body = json.load(resp)
    if body["status"] != "success":
        raise RuntimeError(body)
    result = body["data"]["result"]
    if not result:
        return None
    return float(result[0]["value"][1])


def pull(base_url: str) -> dict[str, float | None]:
    return {name: query(base_url, promql) for name, promql in QUERIES.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://192.168.50.249:9090", help="Prometheus base URL")
    ap.add_argument("--interval", type=float, default=0.0, help="seconds between pulls; 0 = pull once")
    args = ap.parse_args()

    while True:
        try:
            values = pull(args.url)
        except (urllib.error.URLError, RuntimeError) as exc:
            print(f"error: {exc}")
        else:
            ts = time.strftime("%H:%M:%S")
            print(
                f"{ts}  temp={values['cpu_temp_c']:.1f}C"
                f"  load1={values['load1']:.2f}"
                f"  load5={values['load5']:.2f}"
                f"  load15={values['load15']:.2f}"
                f"  cpu={values['cpu_usage_pct']:.1f}%"
            )
        if args.interval <= 0:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

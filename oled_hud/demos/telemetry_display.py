"""HUD playground -- live Prometheus telemetry through the Compositor (H0),
no Store/producers/scheduler scaffolding. CPU temp, load averages and CPU
usage %, pulled from a remote Prometheus/node_exporter and rendered as text.

The Prometheus HTTP call happens on its own cadence (--poll-interval),
separate from the frame loop (--fps): most frames just call `flush()` on an
unchanged `fb` and push nothing, the same settle-to-zero case
`compositor_static.py` covers, except the "static" text actually changes
every few seconds instead of never. A poll failure (panel unreachable,
Prometheus down) prints a warning and keeps showing the last good reading
rather than crashing the loop.

Run with:
    .env/bin/python3 -m oled_hud.demos.telemetry_display
    .env/bin/python3 -m oled_hud.demos.telemetry_display --url http://192.168.50.249:9090
    .env/bin/python3 -m oled_hud.demos.telemetry_display --poll-interval 2 --seconds 60
"""

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request

import board
import busio
import digitalio
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from oled_hud.clock import FrameClock
from oled_hud.driver import PartialSSD1305
from oled_hud.hud.compositor import Compositor
from oled_hud.pack import pack_bits

WIDTH = 128
HEIGHT = 32

QUERIES = {
    "temp": 'node_thermal_zone_temp{type="cpu-thermal"}',
    "load1": "node_load1",
    "load5": "node_load5",
    "cpu": '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)',
}


def query(base_url: str, promql: str, timeout: float = 4.0) -> float | None:
    url = base_url.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": promql})
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body = json.load(resp)
    if body["status"] != "success":
        raise RuntimeError(body)
    result = body["data"]["result"]
    return float(result[0]["value"][1]) if result else None


def pull(base_url: str) -> dict[str, float | None]:
    return {name: query(base_url, promql) for name, promql in QUERIES.items()}


def render_into(comp: Compositor, values: dict[str, float | None]) -> None:
    """All PIL/font work happens here, off the frame path -- called only
    when a poll actually lands, not every frame.
    """
    font = ImageFont.load_default()
    img = Image.new("1", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)
    temp = values["temp"]
    cpu = values["cpu"]
    load1 = values["load1"]
    load5 = values["load5"]
    line1 = f"T:{temp:4.1f}C CPU:{cpu:4.1f}%" if temp is not None and cpu is not None else "no data"
    line2 = f"load {load1:.2f} {load5:.2f}" if load1 is not None and load5 is not None else ""
    draw.text((2, 2), line1, fill=255, font=font)
    draw.text((2, 17), line2, fill=255, font=font)
    bits = pack_bits(np.asarray(img, dtype=np.uint8) > 0)
    comp.fb[...] = bits


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://192.168.50.249:9090", help="Prometheus base URL")
    parser.add_argument("--fps", type=float, default=10.0, help="frame loop rate")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="seconds between Prometheus polls")
    parser.add_argument(
        "--seconds", type=float, default=0.0, help="run time; 0 runs until Ctrl+C"
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = digitalio.DigitalInOut(board.D4)
    display = PartialSSD1305(WIDTH, HEIGHT, i2c, reset=reset_pin)
    display.fill(0)
    display.show()

    comp = Compositor(display)

    clock = FrameClock(args.fps)
    next_poll = 0.0
    polls, poll_errors = 0, 0
    try:
        while not args.seconds or clock.elapsed < args.seconds:
            if clock.elapsed >= next_poll:
                try:
                    render_into(comp, pull(args.url))
                    polls += 1
                except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
                    poll_errors += 1
                    print(f"poll failed, keeping last reading: {exc}")
                next_poll = clock.elapsed + args.poll_interval

            comp.flush()
            clock.tick()
    except KeyboardInterrupt:
        pass
    finally:
        display.fill(0)
        display.show()
        print(clock.summary())
        print(f"polls: {polls} ok, {poll_errors} failed")


if __name__ == "__main__":
    main()

"""HUD playground -- a rotating 3D wireframe (cube or pyramid) through the
Compositor (H0), no PIL, just numpy: rotate a small set of 3D vertices
each frame, project them to 2D with a perspective divide, and rasterize
the edges as line segments straight into `Compositor.fb`.

The panel is 128x32 -- much wider than tall -- so the shape is scaled to
the height (the limiting dimension) and centered horizontally. A sphere
would squash into a short, wide oval at this aspect ratio; a cube or
pyramid's straight edges read clearly even compressed.

Run with:
    .env/bin/python3 -m oled_hud.demos.wireframe
    .env/bin/python3 -m oled_hud.demos.wireframe --shape cube
    .env/bin/python3 -m oled_hud.demos.wireframe --seconds 20 --speed 2.0
"""

import argparse
import signal

import board
import busio
import digitalio
import numpy as np

from oled_hud.clock import FrameClock
from oled_hud.driver import PartialSSD1305
from oled_hud.hud.compositor import Compositor
from oled_hud.pack import pack_bits

WIDTH = 128
HEIGHT = 32

CAM_DIST = 4.0  # camera distance along Z, in the same units as the vertices
FOCAL = 3.0  # focal length -- larger exaggerates the perspective divide

# Vertices centered on the origin, edges as index pairs into them.
CUBE_VERTS = np.array(
    [[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], dtype=np.float64
)
CUBE_EDGES = [
    (i, j)
    for i in range(8)
    for j in range(i + 1, 8)
    if sum(a != b for a, b in zip(CUBE_VERTS[i], CUBE_VERTS[j])) == 1
]

PYRAMID_VERTS = np.array(
    [[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1], [0, 0, 1]], dtype=np.float64
)
PYRAMID_EDGES = [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (1, 4), (2, 4), (3, 4)]

SHAPES = {"cube": (CUBE_VERTS, CUBE_EDGES), "pyramid": (PYRAMID_VERTS, PYRAMID_EDGES)}


def rotation_matrix(ax: float, ay: float) -> np.ndarray:
    cx, sx = np.cos(ax), np.sin(ax)
    cy, sy = np.cos(ay), np.sin(ay)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    return ry @ rx


def project(verts: np.ndarray) -> np.ndarray:
    """Perspective-projects rotated 3D vertices to 2D panel coordinates,
    scaled to HEIGHT (the limiting dimension) and centered on the panel.
    """
    z = verts[:, 2] + CAM_DIST
    scale = (HEIGHT / 2 - 2) * FOCAL / CAM_DIST  # matches on-screen size at z == CAM_DIST
    x2d = verts[:, 0] * FOCAL / z * scale + WIDTH / 2
    y2d = HEIGHT / 2 - verts[:, 1] * FOCAL / z * scale
    return np.stack([x2d, y2d], axis=1)


def draw_wireframe(fb: np.ndarray, points2d: np.ndarray, edges: list[tuple[int, int]]) -> None:
    bits = np.zeros((HEIGHT, WIDTH), dtype=bool)
    for i, j in edges:
        x0, y0 = points2d[i]
        x1, y1 = points2d[j]
        steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        xs = np.round(np.linspace(x0, x1, steps)).astype(np.int64)
        ys = np.round(np.linspace(y0, y1, steps)).astype(np.int64)
        in_bounds = (xs >= 0) & (xs < WIDTH) & (ys >= 0) & (ys < HEIGHT)
        bits[ys[in_bounds], xs[in_bounds]] = True
    fb[...] = pack_bits(bits)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shape", choices=list(SHAPES), default="pyramid")
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--seconds", type=float, default=0.0, help="0 runs until Ctrl+C")
    parser.add_argument("--speed", type=float, default=1.0, help="rotation speed multiplier")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    verts, edges = SHAPES[args.shape]

    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = digitalio.DigitalInOut(board.D4)
    display = PartialSSD1305(WIDTH, HEIGHT, i2c, reset=reset_pin)
    display.fill(0)
    display.show()

    comp = Compositor(display)

    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))

    clock = FrameClock(args.fps)
    try:
        while not stopping and (not args.seconds or clock.elapsed < args.seconds):
            t = clock.elapsed * args.speed
            rotated = verts @ rotation_matrix(t * 0.6, t).T
            draw_wireframe(comp.fb, project(rotated), edges)
            comp.flush()
            clock.tick()
    except KeyboardInterrupt:
        pass
    finally:
        display.fill(0)
        display.show()
        print(clock.summary())


if __name__ == "__main__":
    main()

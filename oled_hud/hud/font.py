"""Bitmap text rendering for HUD views (Phase H1) -- numpy only, no PIL.

The animation-engine handoff's "zero Pillow/font calls in the frame path"
criterion is why this module exists. A vendored font is a generated module
of packed bits (see `scripts/build_fonts.py`), so drawing a string is one
fancy-index into a glyph array plus a reshape: no font file is opened, no
glyph is rasterized, and nothing is cached, because there is nothing
expensive left to cache.

Views draw into a plain (32, 128) bool canvas and pack it once with
`oled_hud.pack.pack_bits`. A bool canvas rather than writing into the
packed framebuffer directly, because text lines land at arbitrary pixel
rows -- only a page-aligned y could be written into `Compositor.fb` without
a read-modify-write, and the HUD's line spacing is not page-aligned.
"""

import base64

import numpy as np

from oled_hud.hud.fonts import fixed4x6, spleen, tomthumb

_MODULES = {"fixed4x6": fixed4x6, "spleen": spleen, "tomthumb": tomthumb}


class Font:
    """A fixed-width bitmap font over ASCII 32..126.

    `advance` is the cell width: the vendored fonts build their inter-character
    gap into the cell (Tom Thumb is a 3x5 glyph in a 4x6 cell), so laying text
    out is just `i * advance` with no kerning table.
    """

    def __init__(self, module):
        self.name = module.NAME
        self.width = module.CELL_W
        self.height = module.CELL_H
        self.advance = module.CELL_W
        self.first = module.FIRST
        self.count = module.COUNT
        raw = np.frombuffer(base64.b64decode(module.DATA), dtype=np.uint8)
        nbits = self.count * self.height * self.width
        self.glyphs = (
            np.unpackbits(raw)[:nbits]
            .reshape(self.count, self.height, self.width)
            .astype(bool)
        )
        self._fallback = ord("?") - self.first

    def indices(self, text: str) -> np.ndarray:
        """Glyph indices for `text`, with anything outside the font's range
        mapped to '?' -- a HUD should show a wrong character, not raise, when
        a producer hands it an unexpected byte.
        """
        codes = np.frombuffer(text.encode("latin-1", "replace"), dtype=np.uint8).astype(np.int32)
        idx = codes - self.first
        return np.where((idx >= 0) & (idx < self.count), idx, self._fallback)

    def measure(self, text: str) -> int:
        """Pixel width `text` would occupy."""
        return len(text) * self.advance

    def render(self, text: str) -> np.ndarray:
        """(height, len(text) * advance) bool array of the rendered string."""
        if not text:
            return np.zeros((self.height, 0), dtype=bool)
        cells = self.glyphs[self.indices(text)]          # (n, h, w)
        return cells.transpose(1, 0, 2).reshape(self.height, -1)

    def draw(self, canvas: np.ndarray, text: str, x: int, y: int) -> None:
        """OR `text` into `canvas` at (x, y), clipped to the canvas.

        Clipping rather than raising: view layout is driven by live values
        whose formatted width isn't known ahead of time, and a string that
        runs off the right edge should truncate on the panel, not take the
        daemon down.
        """
        bits = self.render(text)
        h, w = bits.shape
        ch, cw = canvas.shape
        sx, sy = max(0, -x), max(0, -y)
        ex, ey = min(w, cw - x), min(h, ch - y)
        if ex <= sx or ey <= sy:
            return
        canvas[y + sy : y + ey, x + sx : x + ex] |= bits[sy:ey, sx:ex]


_CACHE: dict[str, Font] = {}


def load(name: str) -> Font:
    """Get a font by name; instances are shared, since a Font is read-only."""
    if name not in _MODULES:
        raise ValueError(f"unknown font {name!r}, have {sorted(_MODULES)}")
    if name not in _CACHE:
        _CACHE[name] = Font(_MODULES[name])
    return _CACHE[name]


def names() -> list[str]:
    return sorted(_MODULES)

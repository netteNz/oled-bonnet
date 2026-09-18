"""HUD daemon package: panel geometry shared by every module under it.

These three numbers describe one physical panel, so they live in one place.
They used to be restated in `views.py`, `daemon.py` and `compositor.py` --
and in two different units, since the compositor thinks in pages where the
view thinks in pixels. `views.render_into` packs a (HEIGHT, WIDTH) canvas
straight into `Compositor.fb`'s (PAGES, WIDTH), so the two have to agree;
deriving PAGES from HEIGHT is what makes that agreement structural instead
of a coincidence two files have to maintain.

Page-major MVLSB means 8 vertical pixels per byte -- see `oled_hud.pack`.
"""

WIDTH = 128
HEIGHT = 32
PAGES = HEIGHT // 8

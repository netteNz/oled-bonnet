# Vendored bitmap fonts

Source BDFs for `scripts/build_fonts.py`. They are committed so the
generated modules under `oled_hud/hud/fonts/` can be rebuilt offline and
byte-for-byte; nothing at runtime reads this directory.

Both were fetched from one upstream so the two files are known to be the
same vintage and format:

    https://raw.githubusercontent.com/olikraus/u8g2/master/tools/font/bdf/

| File | SHA-256 | Cell | License |
|---|---|---|---|
| `spleen-5x8.bdf` | `134e68bd02ffc2022b3c31e15b43cc500df401c80504c2849f445e81a3887cfe` | 5x8 | BSD-2-Clause |
| `tom-thumb.bdf` | `d2c8c15de5ca83fcaef7cadc06d8db578c082507fa3da8a8f698d026fdda2b14` | 4x6 | MIT (as declared in the BDF) |
| `4x6.bdf` | `cc8318b75a92f6209245ac771e891fa1b51a5c64e6eea0e0c85349eb89e8ef8b` | 4x6 | Public domain (as declared in the BDF) |

## Spleen 5x8

Spleen 1.9.1, Copyright (c) 2018-2022 Frederic Cambus
(<https://www.cambus.net/>, <https://github.com/fcambus/spleen>). The BDF's
own `COMMENT` block carries `SPDX-License-Identifier: BSD-2-Clause`; the
full text is in `LICENSE.spleen`, taken from the upstream repository.

## Tom Thumb 4x6

The BDF identifies itself as `Fixed4x6` by foundry `Raccoon` and declares
`COPYRIGHT "MIT"`, but unlike Spleen it carries no author name and no
license text of its own, so only the SPDX identifier above is asserted
here rather than a reproduced notice. If this repo is ever redistributed,
confirm the attribution upstream first.

## X11 misc-fixed 4x6

`-Misc-Fixed-Medium-R-Normal--6-60-75-75-C-40-ISO10646-1`, declaring
`COPYRIGHT "Public domain font.  Share and enjoy."` in the BDF itself.

Vendored as a cross-check on Tom Thumb at the same cell size, and kept
because it is a legitimate third option -- but it is **not** an improvement:
measured over ASCII 32..126 it carries 691 inked pixels against Tom Thumb's
705, in the same 3-wide-glyph-in-a-4px-cell shapes. At five lines on a 32px
panel a glyph gets five rows of cap height, and no typeface fixes that; the
limit is the resolution. See `PROGRESS.md` session 9 for the comparison and
what was done instead.

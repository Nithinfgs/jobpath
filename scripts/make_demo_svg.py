"""Render jobpath's real terminal output to a crisp SVG (docs/assets/demo.svg).

Usage: python scripts/make_demo_svg.py > docs/assets/demo.svg
Bars are drawn as rectangles so alignment does not depend on installed fonts.
"""

from __future__ import annotations

import html
import re
import subprocess
import sys

CW, LH, FS = 8.4, 19, 14
PAD = 20
COLORS = {"": "#e6edf3", "31": "#ff6b6b", "32": "#56d364", "33": "#e3b341", "36": "#4f9fe0", "90": "#6e7681"}
BLOCKS = {"█", "░"}
KEEP = ("jobpath", "median", "TIMELINE", "CRITICAL PATH", "FINDINGS")


def cells(line: str):
    """Yield (char, color, bold, dim) for each visible character."""
    color, bold, dim = "", False, False
    for tok in re.split(r"(\x1b\[[\d;]*m)", line):
        m = re.fullmatch(r"\x1b\[([\d;]*)m", tok)
        if m:
            for code in m.group(1).split(";"):
                if code in ("0", ""):
                    color, bold, dim = "", False, False
                elif code == "1":
                    bold = True
                elif code == "2":
                    dim = True
                else:
                    color = code
        else:
            for ch in tok:
                yield ch, color, bold, dim


def main() -> None:
    raw = subprocess.run(
        [sys.executable, "-m", "jobpath", "--demo", "--color", "--top", "14"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    blocks = [b for b in raw.rstrip().split("\n\n")]
    keep = [b for b in blocks if re.sub(r"\x1b\[[\d;]*m", "", b).lstrip().startswith(KEEP)]
    lines = "\n\n".join(keep).split("\n")
    cols = max(len(re.sub(r"\x1b\[[\d;]*m", "", ln)) for ln in lines)
    w, h = cols * CW + PAD * 2, len(lines) * LH + PAD * 2 + 28
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" role="img" '
        f'aria-label="jobpath terminal output for astral-sh/ruff CI">',
        f'<rect width="{w:.0f}" height="{h:.0f}" rx="10" fill="#0d1117"/>',
        '<circle cx="22" cy="18" r="5.5" fill="#ff5f56"/><circle cx="42" cy="18" r="5.5" fill="#ffbd2e"/>'
        '<circle cx="62" cy="18" r="5.5" fill="#27c93f"/>',
        f'<g font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" font-size="{FS}">',
    ]
    for r, line in enumerate(lines):
        y = PAD + 28 + r * LH
        words, cur = [], None
        for c, (ch, color, bold, dim) in enumerate(cells(line)):
            if ch in BLOCKS:
                fill = COLORS.get(color, COLORS[""])
                op = ".55" if ch == "░" else "1"
                out.append(
                    f'<rect x="{PAD + c * CW:.1f}" y="{y - 12}" width="{CW + 0.3:.1f}" height="{LH - 5}" '
                    f'fill="{fill}" opacity="{op}"/>'
                )
                cur = None
                continue
            if ch == " ":
                cur = None
                continue
            key = (color, bold, dim)
            if cur and cur[0] == key and cur[1] + len(cur[2]) == c:
                cur[2] += ch
            else:
                cur = [key, c, ch]
                words.append(cur)
        for (color, bold, dim), c, text in words:
            fill = COLORS.get(color, COLORS[""])
            extra = (' font-weight="700"' if bold else "") + (' opacity=".6"' if dim else "")
            out.append(
                f'<text x="{PAD + c * CW:.1f}" y="{y}" fill="{fill}"{extra} textLength="{len(text) * CW:.1f}" '
                f'lengthAdjust="spacingAndGlyphs">{html.escape(text)}</text>'
            )
    out.append("</g></svg>")
    print("\n".join(out))


if __name__ == "__main__":
    main()

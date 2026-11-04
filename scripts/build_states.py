"""One-time helper: turn a U.S. states GeoJSON file into map outlines for the page.

You only need to run this again if you change the map projection in albers.py.
Usage:  python scripts/build_states.py path/to/us-states.json
Output: assets/states.json  (one SVG path string per state)
"""

import json
import sys
from pathlib import Path

from albers import HEIGHT, WIDTH, project

ROOT = Path(__file__).resolve().parents[1]
SKIP = {"Alaska", "Hawaii", "Puerto Rico"}


def ring_to_path(ring):
    pts = [project(lon, lat) for lon, lat in ring]
    # Drop points that land on the same spot after rounding (keeps the file small).
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) >= 0.6:
            out.append(p)
    if len(out) < 3:
        return ""
    return "M" + "L".join(f"{x:g},{y:g}" for x, y in out) + "Z"


def main(src):
    geo = json.loads(Path(src).read_text())
    states = []
    xs, ys = [], []
    for feat in geo["features"]:
        name = feat["properties"].get("name")
        if name in SKIP:
            continue
        geom = feat["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        d = ""
        for poly in polys:
            for ring in poly:
                d += ring_to_path(ring)
                for lon, lat in ring:
                    x, y = project(lon, lat)
                    xs.append(x)
                    ys.append(y)
        states.append({"name": name, "d": d})
    print(f"{len(states)} states; x {min(xs):.0f}-{max(xs):.0f} of {WIDTH}, "
          f"y {min(ys):.0f}-{max(ys):.0f} of {HEIGHT}")
    out = ROOT / "assets" / "states.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"width": WIDTH, "height": HEIGHT, "states": states},
                              separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main(sys.argv[1])

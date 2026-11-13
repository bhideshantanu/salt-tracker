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

# The source file has no District of Columbia; its outline (Natural Earth 1:10m admin-1,
# public domain) is added here so DC can be picked and zoomed to on the page.
DC_OUTLINE = [[-77.0229, 38.8028], [-77.0274, 38.8199], [-77.0215, 38.8367], [-77.0178, 38.8452], [-77.0159, 38.8543], [-77.0196, 38.8613], [-77.0259, 38.8726], [-77.0361, 38.8798], [-77.0496, 38.8883], [-77.0601, 38.8991], [-77.0683, 38.9019], [-77.0843, 38.9034], [-77.0972, 38.9082], [-77.1198, 38.9341], [-77.0791, 38.9665], [-77.0419, 38.9961], [-76.9805, 38.9484], [-76.9093, 38.893], [-76.9683, 38.8462], [-77.0229, 38.8028], [-77.0229, 38.8028]]


def ring_to_path(ring, min_step=0.6, digits=None):
    pts = [project(lon, lat) for lon, lat in ring]
    # Drop points that land on the same spot after rounding (keeps the file small).
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) >= min_step:
            out.append(p)
    if len(out) < 3:
        return ""
    fmt = (lambda v: f"{v:.{digits}f}") if digits is not None else (lambda v: f"{v:g}")
    return "M" + "L".join(f"{fmt(x)},{fmt(y)}" for x, y in out) + "Z"


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
    if not any(st["name"] == "District of Columbia" for st in states):
        # DC is only about 4 map units across, so keep fine detail
        states.append({"name": "District of Columbia",
                       "d": ring_to_path(DC_OUTLINE[:-1], min_step=0.02, digits=2)})
    print(f"{len(states)} states; x {min(xs):.0f}-{max(xs):.0f} of {WIDTH}, "
          f"y {min(ys):.0f}-{max(ys):.0f} of {HEIGHT}")
    out = ROOT / "assets" / "states.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"width": WIDTH, "height": HEIGHT, "states": states},
                              separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main(sys.argv[1])

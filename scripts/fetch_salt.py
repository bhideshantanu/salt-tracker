"""Salt season tracker: fetch daily stream salinity from USGS and build data/salt.json.

What it does, in plain words
1. Downloads the daily mean specific conductance (USGS parameter 00095, a proxy for
   dissolved salt) at every USGS stream sensor in the lower 48 states.
2. Works out each stream's "normal" level: the median of its May-October daily
   values, when road salt isn't being spread.
3. Saves the last 150 days of values plus each stream's normal level to
   data/salt.json. The web page compares each day to normal ("3x its summer level").

Runs
  python scripts/fetch_salt.py           daily update (fast: only the last 21 days)
  python scripts/fetch_salt.py --full    rebuild everything, including normal levels
  python scripts/fetch_salt.py --demo    write SYNTHETIC test data for previewing the page

A full rebuild also happens automatically when data/salt.json is missing or its
normal levels are more than 30 days old.

Optional: set the environment variable API_USGS_PAT to a free USGS API key
(https://api.waterdata.usgs.gov/signup/) for higher rate limits.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import random
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from albers import in_conus, project  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data" / "salt.json"

PARAM_SC = "00095"  # specific conductance, microsiemens per cm at 25 C
STAT_MEAN = "00003"  # daily mean

DISPLAY_DAYS = 150  # days kept for the map and chart
RECENT_DAYS = 21  # days re-downloaded on a daily update (catches revisions)
BASELINE_LOOKBACK_DAYS = 400  # always contains one full May-October season
BASELINE_MONTHS = {5, 6, 7, 8, 9, 10}
BASELINE_MIN_DAYS = 60  # a stream needs this many summer days to get a normal level
BASELINE_MAX_US = 3000.0  # higher "normal" usually means tidal or naturally saline water
REBUILD_AFTER_DAYS = 30

# The lower 48 split into five boxes so each download stays a manageable size.
TILES = [
    [-125.0, 24.0, -110.0, 49.6],
    [-110.0, 24.0, -97.0, 49.6],
    [-97.0, 24.0, -87.0, 49.6],
    [-87.0, 24.0, -80.0, 49.6],
    [-80.0, 24.0, -66.5, 49.6],
]

STATE_ABBR = {
    "Alabama": "AL", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}

log = logging.getLogger("salt")


# ---------------------------------------------------------------- downloading


def _with_retries(fn, what, tries=3):
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as exc:  # network hiccups, rate limits, server errors
            log.warning("%s failed (attempt %d/%d): %s", what, attempt, tries, exc)
            if attempt == tries:
                raise
            time.sleep(30 * attempt)


def fetch_daily(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Daily mean specific conductance for all stream sensors, as site/date/sc rows."""
    from dataretrieval import waterdata

    frames = []
    for bbox in TILES:
        def call(bbox=bbox):
            df, _ = waterdata.get_daily(
                parameter_code=PARAM_SC,
                statistic_id=STAT_MEAN,
                time=f"{start.isoformat()}/{end.isoformat()}",
                bbox=bbox,
                skip_geometry=True,
                properties=["monitoring_location_id", "time", "value"],
            )
            return df

        try:
            df = _with_retries(call, f"daily values for box {bbox}")
        except Exception:
            log.error("Skipping box %s after repeated failures", bbox)
            continue
        log.info("box %s: %d rows", bbox, len(df))
        frames.append(df)

    if not frames:
        raise RuntimeError("No data downloaded from USGS")
    return tidy_daily(pd.concat(frames, ignore_index=True))


def tidy_daily(raw: pd.DataFrame) -> pd.DataFrame:
    """Standardize the downloaded table to columns site, date (YYYY-MM-DD), sc."""
    df = pd.DataFrame({
        "site": raw["monitoring_location_id"].astype(str),
        "date": pd.to_datetime(raw["time"], utc=False).dt.strftime("%Y-%m-%d"),
        "sc": pd.to_numeric(raw["value"], errors="coerce"),
    })
    df = df[(df["sc"] > 0) & df["sc"].notna()]
    # Tiles share edges, so a site can appear twice: keep one value per site and day.
    return df.drop_duplicates(["site", "date"], keep="last").reset_index(drop=True)


def fetch_site_info(site_ids: list[str]) -> dict[str, dict]:
    """Name, state, site type and map position for each monitoring location."""
    from dataretrieval import waterdata

    info = {}
    ids = sorted(set(site_ids))
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]

        def call(chunk=chunk):
            df, _ = waterdata.get_monitoring_locations(
                monitoring_location_id=chunk,
                properties=["monitoring_location_id", "monitoring_location_name",
                            "state_name", "site_type_code", "site_type"],
            )
            return df

        df = _with_retries(call, f"site info {i}-{i + len(chunk)}")
        for row in df.to_dict("records"):
            lon, lat = _lon_lat(row.get("geometry"))
            info[str(row["monitoring_location_id"])] = {
                "name": _clean_name(row.get("monitoring_location_name")),
                "state": STATE_ABBR.get(row.get("state_name"), ""),
                "type_code": row.get("site_type_code") or "",
                "type": row.get("site_type") or "",
                "lon": lon,
                "lat": lat,
            }
    return info


def _lon_lat(geom):
    """Geometry arrives as a shapely Point (with geopandas) or a [lon, lat] list."""
    if geom is None:
        return None, None
    if hasattr(geom, "x"):
        return float(geom.x), float(geom.y)
    try:
        return float(geom[0]), float(geom[1])
    except (TypeError, IndexError, ValueError):
        return None, None


def _clean_name(name):
    if not isinstance(name, str):
        return ""
    return " ".join(name.split()).title().replace(" Nr ", " near ").replace(" At ", " at ")


# ---------------------------------------------------------------- calculations


def compute_baselines(daily: pd.DataFrame) -> dict[str, float]:
    """Median May-October daily value per site (its 'normal' salinity)."""
    months = pd.to_datetime(daily["date"]).dt.month
    summer = daily[months.isin(BASELINE_MONTHS)]
    stats = summer.groupby("site")["sc"].agg(["median", "count"])
    stats = stats[(stats["count"] >= BASELINE_MIN_DAYS) & (stats["median"] <= BASELINE_MAX_US)]
    return {site: round(float(m), 1) for site, m in stats["median"].items()}


def keep_site(meta: dict | None) -> bool:
    """Streams only (no tidal reaches, estuaries, lakes or wells), lower 48 only."""
    if not meta or meta.get("lon") is None:
        return False
    code = meta.get("type_code") or ""
    is_stream = code == "ST" if code else meta.get("type") == "Stream"
    return is_stream and in_conus(meta["lon"], meta["lat"])


def date_window(end: dt.date, days: int) -> list[str]:
    return [(end - dt.timedelta(days=days - 1 - i)).isoformat() for i in range(days)]


def assemble(values: dict[str, dict[str, float]], sites: dict[str, dict],
             baselines: dict[str, float], end: dt.date) -> dict:
    """Build the JSON structure the page reads."""
    dates = date_window(end, DISPLAY_DAYS)
    out_sites = []
    for site_id, meta in sorted(sites.items()):
        base = baselines.get(site_id)
        series = values.get(site_id, {})
        sc = [series.get(d) for d in dates]
        if base is None or not any(v is not None for v in sc[-30:]):
            continue  # no normal level yet, or silent for the last month
        x, y = project(meta["lon"], meta["lat"])
        out_sites.append({
            "id": site_id, "name": meta["name"], "state": meta["state"],
            "x": x, "y": y, "lon": round(meta["lon"], 4), "lat": round(meta["lat"], 4),
            "base": base,
            "sc": [None if v is None else round(v) for v in sc],
        })
    return {"dates": dates, "sites": out_sites}


# ---------------------------------------------------------------- saving


def write_output(payload: dict, path: Path = OUT_PATH) -> None:
    """One site per line, so each day's git commit stores only small changes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    head = {k: v for k, v in payload.items() if k != "sites"}
    lines = [json.dumps(s, separators=(",", ":")) for s in payload["sites"]]
    text = json.dumps(head, separators=(",", ":"))[:-1]
    text += ',"sites":[\n' + ",\n".join(lines) + "\n]}\n"
    path.write_text(text)
    log.info("wrote %s: %d sites, %.0f KB", path, len(lines), path.stat().st_size / 1024)


def load_previous(path: Path = OUT_PATH) -> dict | None:
    if not path.exists():
        return None
    try:
        prev = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return None if prev.get("demo") else prev


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def run_full(today: dt.date) -> dict:
    start = today - dt.timedelta(days=BASELINE_LOOKBACK_DAYS)
    log.info("Full rebuild: %s to %s", start, today)
    daily = fetch_daily(start, today)
    baselines = compute_baselines(daily)
    sites_meta = fetch_site_info(list(baselines))
    sites = {s: m for s, m in sites_meta.items() if keep_site(m)}
    values = _to_values(daily[daily["site"].isin(sites)])
    payload = assemble(values, sites, baselines, _last_full_day(daily, today))
    payload["baseline"] = {
        "months": "May-Oct", "computed_utc": _now_utc(),
        "from": start.isoformat(), "to": today.isoformat(),
    }
    payload["site_meta"] = None
    return payload


def run_update(prev: dict, today: dt.date) -> dict:
    start = today - dt.timedelta(days=RECENT_DAYS)
    log.info("Daily update: %s to %s", start, today)
    daily = fetch_daily(start, today)
    known = {s["id"]: s for s in prev["sites"]}
    values = {sid: dict(zip(prev["dates"], s["sc"])) for sid, s in known.items()}
    for site_id, series in _to_values(daily[daily["site"].isin(known)]).items():
        values[site_id].update(series)
    sites = {sid: {"name": s["name"], "state": s["state"], "lon": s["lon"], "lat": s["lat"]}
             for sid, s in known.items()}
    baselines = {sid: s["base"] for sid, s in known.items()}
    payload = assemble(values, sites, baselines, _last_full_day(daily, today))
    payload["baseline"] = prev["baseline"]
    return payload


def _to_values(daily: pd.DataFrame) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for site, date, sc in daily[["site", "date", "sc"]].itertuples(index=False):
        out.setdefault(site, {})[date] = float(sc)
    return out


def _last_full_day(daily: pd.DataFrame, today: dt.date) -> dt.date:
    """The latest date that at least a quarter of sensors have reported."""
    counts = daily.groupby("date")["site"].nunique()
    if counts.empty:
        return today - dt.timedelta(days=1)
    good = counts[counts >= 0.25 * counts.max()]
    return dt.date.fromisoformat(good.index.max())


def needs_full(prev: dict | None, today: dt.date) -> bool:
    if prev is None:
        return True
    computed = prev.get("baseline", {}).get("computed_utc")
    if not computed:
        return True
    age = today - dt.datetime.fromisoformat(computed).date()
    return age.days > REBUILD_AFTER_DAYS


# ---------------------------------------------------------------- demo data


def _state_rings() -> list[list[tuple[float, float]]]:
    """Projected state outlines from assets/states.json, as lists of points."""
    states = json.loads((ROOT / "assets" / "states.json").read_text())["states"]
    rings = []
    for st in states:
        for part in st["d"].split("Z"):
            part = part.strip().lstrip("M")
            if part:
                rings.append([tuple(map(float, p.split(","))) for p in part.split("L")])
    return rings


def _inside(x: float, y: float, rings) -> bool:
    hits = 0
    for ring in rings:
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                hits += 1
            j = i
    return hits % 2 == 1


def make_demo(today: dt.date, seed: int = 7) -> dict:
    """SYNTHETIC data for previewing the page offline. Not real measurements.

    Uses a winter window (ending March 31) so the preview shows salt-season behavior.
    """
    rng = random.Random(seed)
    end = dt.date(today.year if today.month > 3 else today.year - 1, 3, 31)
    dates = date_window(end, DISPLAY_DAYS)
    storms = [d for d in range(DISPLAY_DAYS) if 25 < d < 135 and rng.random() < 0.07]
    rings = _state_rings()
    sites = []
    i = 0
    while len(sites) < 1100 and i < 20000:
        i += 1
        lon = rng.uniform(-124, -67.5)
        lat = rng.uniform(25, 49)
        x, y = project(lon, lat)
        if not _inside(x, y, rings):
            continue
        # sensors cluster in the east, like the real network
        if lon < -100 and rng.random() < 0.55:
            continue
        snowy = lat > 37.5 and lon > -105
        urban = rng.random() < (0.45 if snowy else 0.15)
        base = round(rng.lognormvariate(math.log(350), 0.6), 1)
        level = 1.0
        sc = []
        for d in range(DISPLAY_DAYS):
            if snowy and d in storms:
                level += rng.uniform(1, 9) if urban else rng.uniform(0, 0.8)
            level = 1 + (level - 1) * (0.82 if urban else 0.9)
            noise = rng.uniform(0.92, 1.08)
            sc.append(None if rng.random() < 0.03 else round(base * level * noise))
        sites.append({"id": f"TEST-{i:05d}", "name": f"Synthetic test site {len(sites):04d}",
                      "state": "", "x": x, "y": y, "lon": round(lon, 3),
                      "lat": round(lat, 3), "base": base, "sc": sc})
    return {"demo": True, "dates": dates, "sites": sites,
            "baseline": {"months": "May-Oct", "computed_utc": _now_utc()}}


# ---------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--full", action="store_true", help="rebuild everything")
    ap.add_argument("--demo", action="store_true", help="write synthetic test data")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    today = dt.datetime.now(dt.timezone.utc).date()
    if args.demo:
        payload = make_demo(today)
    else:
        prev = load_previous()
        payload = run_full(today) if (args.full or needs_full(prev, today)) else run_update(prev, today)
        payload["demo"] = False
    payload.pop("site_meta", None)
    payload["generated_utc"] = _now_utc()
    payload["source"] = "USGS Water Data API, daily mean specific conductance (00095)"
    write_output(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Salt season tracker: fetch daily stream salinity and flow from USGS.

What it does, in plain words
1. Downloads the daily mean specific conductance (USGS parameter 00095, a proxy for
   dissolved salt) at every USGS stream sensor in the lower 48 states.
2. Works out each stream's "normal" level: the median of its May-October daily
   values, when road salt isn't being spread.
3. Downloads the daily mean streamflow (parameter 00060) at the same sensors, where
   it is measured, so the page can tell a runoff flush from low-flow concentration.
4. Saves the last 185 days to data/salt.json. The web page compares each day to
   normal ("3x its summer level").
5. Keeps one archive file per finished winter (November to April), for example
   data/season-2025-26.json, listed in data/seasons.json. The archive for the most
   recent finished winter is built automatically the first time it is missing.

Runs
  python scripts/fetch_salt.py               daily update (fast: only the last 21 days)
  python scripts/fetch_salt.py --full        rebuild the live data, including normal levels
  python scripts/fetch_salt.py --season 2024 (re)build the archive for winter 2024-25 only
  python scripts/fetch_salt.py --demo        write SYNTHETIC test data for previewing the page

A full rebuild also happens automatically when data/salt.json is missing, was made by
an older version of this script, or its normal levels are more than 30 days old.

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
import re
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from albers import in_conus, project  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_PATH = DATA_DIR / "salt.json"
SEASONS_INDEX = DATA_DIR / "seasons.json"

FORMAT_VERSION = 2  # bump when the file layout changes; older files trigger a rebuild

PARAM_SC = "00095"  # specific conductance, microsiemens per cm at 25 C
PARAM_Q = "00060"  # discharge, cubic feet per second
STAT_MEAN = "00003"  # daily mean

DISPLAY_DAYS = 185  # days kept for the live map and chart (covers a whole Nov-Apr winter)
RECENT_DAYS = 21  # days re-downloaded on a daily update (catches revisions)
BASELINE_LOOKBACK_DAYS = 400  # always contains one full May-October season
BASELINE_MONTHS = {5, 6, 7, 8, 9, 10}
BASELINE_MIN_DAYS = 60  # a stream needs this many summer days to get a normal level
BASELINE_MAX_US = 3000.0  # higher "normal" usually means tidal or naturally saline water
REBUILD_AFTER_DAYS = 30
SEASON_MIN_DAYS = 30  # an archived stream needs this many winter readings
ID_CHUNK = 300  # sites per streamflow request

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


def fetch_daily(start: dt.date, end: dt.date, strict: bool = False) -> pd.DataFrame:
    """Daily mean specific conductance for all stream sensors, as site/date/sc rows.

    strict=True (used for archives) stops on any failed box instead of skipping it,
    so an archive is never saved with a region missing.
    """
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
            if strict:
                raise
            log.error("Skipping box %s after repeated failures", bbox)
            continue
        log.info("box %s: %d rows", bbox, len(df))
        frames.append(df)

    if not frames:
        raise RuntimeError("No data downloaded from USGS")
    return tidy_daily(pd.concat(frames, ignore_index=True))


def fetch_flow(site_ids, start: dt.date, end: dt.date, strict: bool = False) -> pd.DataFrame:
    """Daily mean streamflow (cfs) at the given sites, as site/date/q rows.

    Many salt sensors have no flow gage, and northern gages often skip ice-affected
    winter days, so gaps are normal. A failed chunk is skipped rather than stopping
    the whole run: the page simply shows "no flow data" for those streams.
    """
    from dataretrieval import waterdata

    ids = sorted(set(site_ids))
    frames = []
    for i in range(0, len(ids), ID_CHUNK):
        chunk = ids[i:i + ID_CHUNK]

        def call(chunk=chunk):
            df, _ = waterdata.get_daily(
                monitoring_location_id=chunk,
                parameter_code=PARAM_Q,
                statistic_id=STAT_MEAN,
                time=f"{start.isoformat()}/{end.isoformat()}",
                skip_geometry=True,
                properties=["monitoring_location_id", "time", "value"],
            )
            return df

        try:
            df = _with_retries(call, f"streamflow for sites {i}-{i + len(chunk)}")
        except Exception:
            if strict:
                raise
            log.error("Skipping streamflow for sites %d-%d after repeated failures",
                      i, i + len(chunk))
            continue
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["site", "date", "q"])
    flow = tidy_daily(pd.concat(frames, ignore_index=True), column="q")
    log.info("streamflow: %d rows at %d sites", len(flow), flow["site"].nunique())
    return flow


def tidy_daily(raw: pd.DataFrame, column: str = "sc") -> pd.DataFrame:
    """Standardize the downloaded table to columns site, date (YYYY-MM-DD), value."""
    if raw.empty:
        return pd.DataFrame(columns=["site", "date", column])
    df = pd.DataFrame({
        "site": raw["monitoring_location_id"].astype(str),
        "date": pd.to_datetime(raw["time"], utc=False).dt.strftime("%Y-%m-%d"),
        column: pd.to_numeric(raw["value"], errors="coerce"),
    })
    df = df[(df[column] > 0) & df[column].notna()]
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


_SMALL_WORDS = {"Nr": "near", "Near": "near", "At": "at", "Abv": "above", "Above": "above",
                "Blw": "below", "Below": "below", "Of": "of", "And": "and", "The": "the",
                "On": "on", "In": "in"}


def _clean_name(name):
    """'STILLWATER RIVER NEAR STERLING, MA' -> 'Stillwater River near Sterling, MA'."""
    if not isinstance(name, str):
        return ""
    words = " ".join(name.split()).title().split(" ")
    words = [words[0]] + [_SMALL_WORDS.get(w, w) for w in words[1:]]
    out = " ".join(words)
    # keep a trailing two-letter state code in capitals
    return re.sub(r",\s*([A-Za-z]{2})\.?$", lambda m: ", " + m.group(1).upper(), out)


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


def date_range(start: dt.date, end: dt.date) -> list[str]:
    return date_window(end, (end - start).days + 1)


def _sig3(v):
    """Round a flow to 3 significant figures (1234.5 -> 1230, 0.04567 -> 0.0457)."""
    if v is None or not (v > 0) or math.isinf(v):
        return None
    digits = 2 - int(math.floor(math.log10(v)))
    r = round(v, digits)
    return int(r) if digits <= 0 else r


def assemble(values: dict[str, dict[str, float]], sites: dict[str, dict],
             baselines: dict[str, float], dates: list[str],
             flows: dict[str, dict[str, float]] | None = None,
             archive: bool = False) -> dict:
    """Build the JSON structure the page reads.

    Live data keeps streams that reported in the last 30 days; an archive keeps
    streams with at least SEASON_MIN_DAYS readings over the winter.
    """
    flows = flows or {}
    out_sites = []
    for site_id, meta in sorted(sites.items()):
        base = baselines.get(site_id)
        series = values.get(site_id, {})
        sc = [series.get(d) for d in dates]
        if base is None:
            continue  # no normal level
        if archive:
            if sum(v is not None for v in sc) < SEASON_MIN_DAYS:
                continue
        elif not any(v is not None for v in sc[-30:]):
            continue  # silent for the last month
        x, y = project(meta["lon"], meta["lat"])
        rec = {
            "id": site_id, "name": meta["name"], "state": meta["state"],
            "x": x, "y": y, "lon": round(meta["lon"], 4), "lat": round(meta["lat"], 4),
            "base": base,
            "sc": [None if v is None else round(v) for v in sc],
        }
        fseries = flows.get(site_id)
        if fseries:
            q = [_sig3(fseries.get(d)) for d in dates]
            if any(v is not None for v in q):
                rec["q"] = q
        out_sites.append(rec)
    return {"dates": dates, "sites": out_sites}


# ---------------------------------------------------------------- saving


def write_output(payload: dict, path: Path = OUT_PATH) -> None:
    """One site per line, so each day's git commit stores only small changes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    head = {k: v for k, v in payload.items() if k != "sites"}
    lines = [json.dumps(s, separators=(",", ":")) for s in payload["sites"]]
    text = json.dumps(head, separators=(",", ":"))[:-1]
    text += ',"sites":[\n' + ",\n".join(lines) + "\n]}\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(path)  # never leave a half-written file behind
    n_flow = sum(1 for s in payload["sites"] if "q" in s)
    log.info("wrote %s: %d sites (%d with streamflow), %.0f KB",
             path.name, len(lines), n_flow, path.stat().st_size / 1024)


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


def _to_values(daily: pd.DataFrame, column: str = "sc") -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for site, date, v in daily[["site", "date", column]].itertuples(index=False):
        out.setdefault(site, {})[date] = float(v)
    return out


def _last_full_day(daily: pd.DataFrame, today: dt.date) -> dt.date:
    """The latest date that at least a quarter of sensors have reported."""
    counts = daily.groupby("date")["site"].nunique()
    if counts.empty:
        return today - dt.timedelta(days=1)
    good = counts[counts >= 0.25 * counts.max()]
    return dt.date.fromisoformat(good.index.max())


def run_full(today: dt.date) -> dict:
    start = today - dt.timedelta(days=BASELINE_LOOKBACK_DAYS)
    log.info("Full rebuild: %s to %s", start, today)
    daily = fetch_daily(start, today)
    baselines = compute_baselines(daily)
    sites_meta = fetch_site_info(list(baselines))
    sites = {s: m for s, m in sites_meta.items() if keep_site(m)}
    values = _to_values(daily[daily["site"].isin(sites)])
    dates = date_window(_last_full_day(daily, today), DISPLAY_DAYS)
    flow = fetch_flow(sites, dt.date.fromisoformat(dates[0]), today)
    payload = assemble(values, sites, baselines, dates, flows=_to_values(flow, "q"))
    payload["baseline"] = {
        "months": "May-Oct", "computed_utc": _now_utc(),
        "from": start.isoformat(), "to": today.isoformat(),
    }
    return payload


def run_update(prev: dict, today: dt.date) -> dict:
    start = today - dt.timedelta(days=RECENT_DAYS)
    log.info("Daily update: %s to %s", start, today)
    daily = fetch_daily(start, today)
    known = {s["id"]: s for s in prev["sites"]}
    values = {sid: dict(zip(prev["dates"], s["sc"])) for sid, s in known.items()}
    for site_id, series in _to_values(daily[daily["site"].isin(known)]).items():
        values[site_id].update(series)
    flows = {sid: {d: v for d, v in zip(prev["dates"], s.get("q") or []) if v is not None}
             for sid, s in known.items()}
    flow = fetch_flow(known, start, today)
    for site_id, series in _to_values(flow, "q").items():
        flows.setdefault(site_id, {}).update(series)
    sites = {sid: {"name": _clean_name(s["name"]), "state": s["state"],
                   "lon": s["lon"], "lat": s["lat"]}
             for sid, s in known.items()}
    baselines = {sid: s["base"] for sid, s in known.items()}
    dates = date_window(_last_full_day(daily, today), DISPLAY_DAYS)
    payload = assemble(values, sites, baselines, dates, flows=flows)
    payload["baseline"] = prev["baseline"]
    return payload


def needs_full(prev: dict | None, today: dt.date) -> bool:
    if prev is None:
        return True
    if prev.get("version", 1) < FORMAT_VERSION:
        log.info("data/salt.json was made by an older version of this script: rebuilding")
        return True
    computed = prev.get("baseline", {}).get("computed_utc")
    if not computed:
        return True
    age = today - dt.datetime.fromisoformat(computed).date()
    return age.days > REBUILD_AFTER_DAYS


# ---------------------------------------------------------------- winter archives


def season_id(start_year: int) -> str:
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def season_label(start_year: int) -> str:
    return f"Winter {start_year}–{(start_year + 1) % 100:02d}"


def season_path(start_year: int) -> Path:
    return DATA_DIR / f"season-{season_id(start_year)}.json"


def latest_finished_season(today: dt.date) -> int:
    """Start year of the most recent winter that has fully ended (on April 30)."""
    return today.year - 1 if today.month >= 5 else today.year - 2


def build_season(start_year: int) -> dict:
    """Archive one winter: November 1 to April 30, compared with the summer before it."""
    y = start_year
    fetch_start, win_start, win_end = dt.date(y, 5, 1), dt.date(y, 11, 1), dt.date(y + 1, 4, 30)
    log.info("Building the %s archive: %s to %s", season_label(y), fetch_start, win_end)
    daily = fetch_daily(fetch_start, win_end, strict=True)
    baselines = compute_baselines(daily)  # May-October of the same year
    sites_meta = fetch_site_info(list(baselines))
    sites = {s: m for s, m in sites_meta.items() if keep_site(m)}
    values = _to_values(daily[daily["site"].isin(sites)])
    dates = date_range(win_start, win_end)
    flow = fetch_flow(sites, win_start, win_end, strict=True)
    payload = assemble(values, sites, baselines, dates,
                       flows=_to_values(flow, "q"), archive=True)
    payload["baseline"] = {"months": f"May-Oct {y}", "computed_utc": _now_utc()}
    return payload


def finish_payload(payload: dict, demo: bool, season: int | None = None) -> dict:
    payload["version"] = FORMAT_VERSION
    payload["demo"] = demo
    if season is not None:
        payload["season"] = season_id(season)
        payload["label"] = season_label(season)
    payload["generated_utc"] = _now_utc()
    payload["source"] = ("USGS Water Data API, daily mean specific conductance (00095) "
                         "and discharge (00060)")
    return payload


def write_seasons_index() -> None:
    """List every archived winter, newest first, for the page's season picker."""
    seasons = []
    for path in sorted(DATA_DIR.glob("season-*.json"), reverse=True):
        try:
            head = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("Ignoring unreadable archive %s", path.name)
            continue
        seasons.append({"id": head.get("season", path.stem[7:]),
                        "label": head.get("label", path.stem),
                        "file": f"data/{path.name}",
                        "sites": len(head.get("sites", [])),
                        "demo": bool(head.get("demo"))})
    SEASONS_INDEX.write_text(json.dumps({"seasons": seasons}, indent=1) + "\n")
    log.info("wrote %s: %s", SEASONS_INDEX.name, ", ".join(s["id"] for s in seasons) or "none")


def archive_season(start_year: int) -> None:
    payload = finish_payload(build_season(start_year), demo=False, season=start_year)
    if not payload["sites"]:
        raise RuntimeError(f"No streams qualified for the {season_label(start_year)} archive")
    write_output(payload, season_path(start_year))
    write_seasons_index()


def ensure_latest_archive(today: dt.date) -> None:
    """Build last winter's archive if it is missing. Never stops the live update."""
    y = latest_finished_season(today)
    path = season_path(y)
    if path.exists():
        try:
            if not json.loads(path.read_text()).get("demo"):
                if not SEASONS_INDEX.exists():
                    write_seasons_index()
                return
        except json.JSONDecodeError:
            pass
    try:
        archive_season(y)
    except Exception as exc:
        log.error("Could not build the %s archive this time (%s). The live data was "
                  "saved; tomorrow's run will try the archive again.", season_label(y), exc)


# ---------------------------------------------------------------- demo data


def _state_shapes() -> list[tuple[str, list[list[tuple[float, float]]]]]:
    """Projected state outlines from assets/states.json: (abbreviation, rings)."""
    states = json.loads((ROOT / "assets" / "states.json").read_text())["states"]
    out = []
    for st in states:
        rings = []
        for part in st["d"].split("Z"):
            part = part.strip().lstrip("M")
            if part:
                rings.append([tuple(map(float, p.split(","))) for p in part.split("L")])
        out.append((STATE_ABBR.get(st["name"], ""), rings))
    return out


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


def _demo_sites(rng: random.Random, n: int = 1100) -> list[dict]:
    shapes = _state_shapes()
    sites, i = [], 0
    while len(sites) < n and i < 30000:
        i += 1
        lon, lat = rng.uniform(-124, -67.5), rng.uniform(25, 49)
        if lon < -100 and rng.random() < 0.55:
            continue  # sensors cluster in the east, like the real network
        x, y = project(lon, lat)
        state = next((ab for ab, rings in shapes if _inside(x, y, rings)), None)
        if state is None:
            continue
        snowy = lat > 37.5 and lon > -105
        urban = rng.random() < (0.45 if snowy else 0.15)
        sites.append({
            "id": f"TEST-{i:05d}", "name": f"Synthetic test site {len(sites):04d}",
            "state": state, "x": x, "y": y, "lon": round(lon, 3), "lat": round(lat, 3),
            "base": round(rng.lognormvariate(math.log(350), 0.6), 1),
            "snowy": snowy, "urban": urban,
            "decay": rng.uniform(0.72, 0.93) if urban else rng.uniform(0.8, 0.92),
            "legacy": rng.uniform(0.2, 1.6) if urban and rng.random() < 0.5 else 0.0,
            "qbase": rng.lognormvariate(math.log(60), 1.3) if rng.random() < 0.75 else None,
        })
    return sites


def _demo_series(rng, site, dates, storms):
    """SC and flow for one synthetic site. Storms flush salt in with high flow; legacy
    sites also creep upward between storms at low flow, like stored salt in baseflow."""
    level, qlevel, sc, q = 1.0, 1.0, [], []
    for d in dates:
        day = dt.date.fromisoformat(d)
        in_season = day.month in (11, 12, 1, 2, 3, 4)
        since_nov = (day - dt.date(day.year if day.month >= 11 else day.year - 1, 11, 1)).days
        floor = 1 + site["legacy"] * min(max(since_nov, 0), 120) / 120 if in_season else 1.0
        for (lon, lat, radius, strength) in storms.get(d, []):
            near = math.hypot(site["lon"] - lon, (site["lat"] - lat) * 1.3) < radius
            if near:
                qlevel += rng.uniform(1.5, 5) * strength
                if site["snowy"] and in_season:
                    level += (rng.uniform(1, 9) if site["urban"] else rng.uniform(0, 0.8)) * strength
        level = floor + (max(level, floor) - floor) * site["decay"]
        qlevel = 1 + (qlevel - 1) * 0.6
        winter_q = 0.75 if in_season and site["legacy"] else 1.0  # dry spells for legacy sites
        sc.append(None if rng.random() < 0.03 else round(site["base"] * level * rng.uniform(0.92, 1.08)))
        if site["qbase"] is None or (in_season and site["lat"] > 44 and rng.random() < 0.2):
            q.append(None)  # no gage, or ice-affected
        else:
            q.append(_sig3(site["qbase"] * qlevel * winter_q * rng.uniform(0.85, 1.15)))
    return sc, q


def _demo_storms(rng, dates, n_season_days):
    """Random regional storms in the salt season, plus two big ones."""
    storms = {}
    for d in dates:
        day = dt.date.fromisoformat(d)
        if day.month in (11, 12, 1, 2, 3, 4) and rng.random() < 0.09:
            storms[d] = [(rng.uniform(-97, -70), rng.uniform(38, 46), rng.uniform(4, 9),
                          rng.uniform(0.6, 1.0))]
    for d in rng.sample([d for d in dates if dt.date.fromisoformat(d).month in (1, 2)],
                        k=min(2, n_season_days)):
        storms[d] = [(-80, 41, 14, 1.0)]
    return storms


def make_demo(today: dt.date, seed: int = 7) -> tuple[dict, dict, int]:
    """SYNTHETIC data for previewing the page offline. Not real measurements.

    Returns the live payload (ending yesterday), an archive payload for last winter,
    and that winter's start year.
    """
    rng = random.Random(seed)
    sites = _demo_sites(rng)
    y = latest_finished_season(today)
    live_dates = date_window(today - dt.timedelta(days=1), DISPLAY_DAYS)
    arch_dates = date_range(dt.date(y, 11, 1), dt.date(y + 1, 4, 30))
    out = []
    for dates in (live_dates, arch_dates):
        storms = _demo_storms(rng, dates, sum(dt.date.fromisoformat(d).month in (1, 2) for d in dates))
        recs = []
        for s in sites:
            sc, q = _demo_series(rng, s, dates, storms)
            rec = {k: s[k] for k in ("id", "name", "state", "x", "y", "lon", "lat", "base")}
            rec["sc"] = sc
            if any(v is not None for v in q):
                rec["q"] = q
            recs.append(rec)
        out.append({"dates": dates, "sites": recs,
                    "baseline": {"months": "May-Oct", "computed_utc": _now_utc()}})
    return out[0], out[1], y


# ---------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--full", action="store_true", help="rebuild the live data")
    ap.add_argument("--season", type=int, metavar="YEAR",
                    help="(re)build the archive for the winter starting in YEAR, then stop")
    ap.add_argument("--demo", action="store_true", help="write synthetic test data")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    today = dt.datetime.now(dt.timezone.utc).date()
    if args.demo:
        live, arch, y = make_demo(today)
        write_output(finish_payload(live, demo=True))
        write_output(finish_payload(arch, demo=True, season=y), season_path(y))
        write_seasons_index()
        return 0
    if args.season:
        if args.season > latest_finished_season(today):
            log.error("Winter %s has not finished yet.", season_id(args.season))
            return 1
        archive_season(args.season)
        return 0

    prev = load_previous()
    payload = run_full(today) if (args.full or needs_full(prev, today)) else run_update(prev, today)
    write_output(finish_payload(payload, demo=False))
    ensure_latest_archive(today)
    return 0


if __name__ == "__main__":
    sys.exit(main())

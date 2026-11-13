"""Watershed facts for every salt-tracker station.

What it does, in plain words
1. Lists every station on the page: the live data and every winter archive.
2. Asks the USGS Network-Linked Data Index (NLDI) which NHDPlus Version 2.1 stream reach
   (COMID) each gage sits on.
3. Reads that reach's whole-upstream-watershed metrics from EPA's StreamCat dataset
   (Hill and others, 2016, JAWRA): NLCD 2019 land cover and imperviousness, 2010 census
   population density, road density, baseflow index, elevation, soils, PRISM 1981-2010
   climate normals and dam density. (USGS removed the NLDI's own watershed-attribute
   endpoints in 2026, so StreamCat is the source now.)
4. Classifies each watershed the way Chen and Husic (2026, Communications Earth &
   Environment) did: urban, agricultural or forested when that cover is more than half
   of the watershed, otherwise mixed.
5. Saves everything to data/site-attributes.json. Later runs only look up new stations.
   Stations with no match are retried after RETRY_DAYS; failed lookups the next day.

Runs
  python scripts/fetch_attributes.py           look up stations not done yet
  python scripts/fetch_attributes.py --refresh look up every station again

No API key is needed. A failed lookup never stops the daily salt update.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_PATH = DATA_DIR / "site-attributes.json"
NLDI = "https://api.water.usgs.gov/nldi"
STREAMCAT = "https://api.epa.gov/StreamCat/streams/metrics"
RETRY_DAYS = 14  # stations with no match
ERROR_RETRY_DAYS = 1  # stations whose lookup failed
BATCH = 50  # COMIDs per StreamCat request
PAUSE_S = 0.05  # be polite to the services
FORMAT_VERSION = 2

# StreamCat metric names, asked for a few at a time: one request that mixes many of its
# tables comes back empty. The answer adds "ws" (whole watershed) to each name.
GROUPS = [
    ["pcturbop2019", "pcturblo2019", "pcturbmd2019", "pcturbhi2019", "pctimp2019"],
    ["pctdecid2019", "pctconif2019", "pctmxfst2019", "pcthay2019", "pctcrop2019",
     "pctow2019", "pctwdwet2019", "pcthbwet2019"],
    ["bfi", "rddens", "popden2010"],
    ["elev", "clay", "sand", "damdens"],
    ["precip8110", "tmean8110"],
]
URBAN = ("pcturbop2019ws", "pcturblo2019ws", "pcturbmd2019ws", "pcturbhi2019ws")  # NLCD 21-24
FOREST = ("pctdecid2019ws", "pctconif2019ws", "pctmxfst2019ws")  # NLCD 41-43
AG = ("pcthay2019ws", "pctcrop2019ws")  # NLCD 81-82
WATER = ("pctow2019ws",)  # NLCD 11
WETLAND = ("pctwdwet2019ws", "pcthbwet2019ws")  # NLCD 90, 95

# what each saved variable is (copied into the file so the page and CSV can label units)
VARIABLES = {
    "land cover": {"source": "StreamCat PctUrbOp/Lo/Md/Hi, PctDecid/Conif/MxFst, PctHay/Crop, PctOw, PctWdWet/HbWet 2019 (Ws)",
                   "units": "percent of watershed",
                   "description": "NLCD 2019 classes, grouped: developed 21-24, forest 41-43, agriculture 81-82, open water 11, wetlands 90 and 95"},
    "impervious_pct": {"source": "StreamCat PctImp2019Ws", "units": "percent", "description": "Mean imperviousness of the watershed, NLCD 2019"},
    "area_km2": {"source": "StreamCat WsAreaSqKm", "units": "km²", "description": "Watershed area (NHDPlus Version 2.1)"},
    "bfi_pct": {"source": "StreamCat BFIWs", "units": "percent", "description": "Baseflow index: share of streamflow from groundwater (USGS)"},
    "road_density": {"source": "StreamCat RdDensWs", "units": "km per km²", "description": "Road length per area, 2010 Census TIGER roads"},
    "pop_density": {"source": "StreamCat PopDen2010Ws", "units": "people per km²", "description": "Population density, 2010 Census"},
    "elev_m": {"source": "StreamCat ElevWs", "units": "m", "description": "Mean elevation of the watershed"},
    "clay_pct": {"source": "StreamCat ClayWs", "units": "percent", "description": "Mean clay content of soils (STATSGO)"},
    "sand_pct": {"source": "StreamCat SandWs", "units": "percent", "description": "Mean sand content of soils (STATSGO)"},
    "precip_mm": {"source": "StreamCat Precip8110Ws", "units": "mm per year", "description": "PRISM 1981-2010 normal annual precipitation"},
    "temp_c": {"source": "StreamCat Tmean8110Ws", "units": "°C", "description": "PRISM 1981-2010 normal mean annual air temperature"},
    "dam_density": {"source": "StreamCat DamDensWs x 100", "units": "dams per 100 km²", "description": "Density of dams in the National Inventory of Dams"},
}

log = logging.getLogger("attributes")
_session = requests.Session()
_session.headers["User-Agent"] = "salt-season-tracker (shantanubhide.com/salt-tracker)"
_session.headers["Accept"] = "application/json"


# ---------------------------------------------------------------- talking to the services


def _get(url: str, params=None, tries: int = 3, post: bool = False):
    for attempt in range(1, tries + 1):
        try:
            if post:  # form fields in the body, the way EPA's own StreamCat client asks
                r = _session.post(url, data=params, timeout=120)
            else:
                r = _session.get(url, params=params, timeout=120)
            if r.status_code == 404:
                return None  # not found: a normal answer, not an error
            r.raise_for_status()
            return r.json() if r.content else None
        except (requests.RequestException, ValueError) as exc:
            if attempt == tries:
                raise
            log.warning("%s failed (attempt %d/%d): %s", url, attempt, tries, exc)
            time.sleep(5 * attempt)


def find_comid(site_id: str) -> str | None:
    """NHDPlus reach (COMID) of a USGS gage, e.g. USGS-01646500 -> '4512772'."""
    doc = _get(f"{NLDI}/linked-data/nwissite/{site_id}")
    for feat in (doc or {}).get("features", []):
        comid = (feat.get("properties") or {}).get("comid")
        if comid not in (None, ""):
            return str(comid)
    return None


def _rows(doc) -> list[dict]:
    items = (doc or {}).get("items") or []
    if items and all("comid" not in it for it in items):
        raise RuntimeError(f"StreamCat returned no rows: {str(items)[:120]}")
    return items


def streamcat(comids: list[str]) -> dict[str, dict]:
    """Watershed metrics for a batch of COMIDs: comid -> {metric name: value}."""
    out: dict[str, dict] = {}
    for k, names in enumerate(GROUPS):
        params = {"name": ",".join(names), "aoi": "ws", "comid": ",".join(comids)}
        if k == 0:
            params["showareasqkm"] = "true"
        try:
            items = _rows(_get(STREAMCAT, params, post=True))
        except Exception as exc:  # fall back to a plain web address (also supported)
            log.warning("StreamCat POST failed (%s); trying GET", exc)
            items = _rows(_get(STREAMCAT, params))
        for it in items:
            c = str(it.get("comid", "")).split(".")[0]
            if not c:
                continue
            row = out.setdefault(c, {})
            for key, v in it.items():
                if key == "comid" or v is None:
                    continue
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                if v > -9998:  # "no data" code
                    row[key.lower()] = v
        time.sleep(PAUSE_S)
    return out


# ---------------------------------------------------------------- turning metrics into the page's list


def summarize(m: dict) -> dict:
    """StreamCat metrics of one watershed -> the page's short list (missing ones are left out)."""
    out = {}

    def total(keys):
        vals = [m[k] for k in keys if k in m]
        return round(sum(vals), 1) if len(vals) == len(keys) else None

    lc = dict(urban_pct=total(URBAN), forest_pct=total(FOREST), ag_pct=total(AG),
              water_pct=total(WATER), wetland_pct=total(WETLAND))
    if None not in (lc["urban_pct"], lc["forest_pct"], lc["ag_pct"]):
        out.update({k: v for k, v in lc.items() if v is not None}, nlcd_year=2019)
    for key, metric, digits, extra in [
        ("impervious_pct", "pctimp2019ws", 1, {"impervious_year": 2019}),
        ("pop_density", "popden2010ws", 1, {"pop_year": 2010}),
        ("area_km2", "wsareasqkm", 2, {}), ("bfi_pct", "bfiws", 1, {}),
        ("road_density", "rddensws", 2, {}), ("elev_m", "elevws", 0, {}),
        ("clay_pct", "clayws", 1, {}), ("sand_pct", "sandws", 1, {}),
        ("precip_mm", "precip8110ws", 0, {}), ("temp_c", "tmean8110ws", 1, {}),
    ]:
        if metric in m:
            out[key] = round(m[metric], digits) if digits else round(m[metric])
            out.update(extra)
    if "damdensws" in m:  # dams per km2 x 100 km2 = dams per 100 km2 (reads better than 0.0129)
        out["dam_density"] = round(m["damdensws"] * 100, 2)
    out["landuse"] = classify(out)
    return out


def classify(a: dict) -> str | None:
    """Chen and Husic (2026): a class covering more than half of the watershed, else mixed."""
    if "urban_pct" not in a:
        return None
    for key, name in (("urban_pct", "urban"), ("ag_pct", "agricultural"), ("forest_pct", "forested")):
        if a[key] > 50:
            return name
    return "mixed"


# ---------------------------------------------------------------- the stations and the saved file


def station_ids() -> dict[str, dict]:
    """Every station on the page: id -> name/state (from the live file and all archives)."""
    out = {}
    for path in [DATA_DIR / "salt.json", *sorted(DATA_DIR.glob("season-*.json"))]:
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if doc.get("demo"):
            continue
        for s in doc.get("sites", []):
            out.setdefault(s["id"], {"name": s.get("name", ""), "state": s.get("state", "")})
    return out


def load_cache() -> dict:
    try:
        doc = json.loads(OUT_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"version": FORMAT_VERSION, "sites": {}}
    if doc.get("version") == FORMAT_VERSION:
        return doc
    # older file: keep the stream-reach matches, look the watershed facts up again
    sites = {}
    for sid, rec in (doc.get("sites") or {}).items():
        if rec.get("comid"):
            sites[sid] = {"comid": str(rec["comid"]), "status": "pending"}
        elif rec.get("status") == "not indexed":
            sites[sid] = {"status": "not indexed", "checked_utc": rec.get("checked_utc")}
    return {"version": FORMAT_VERSION, "sites": sites}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def needs_lookup(rec: dict | None, refresh: bool) -> bool:
    if refresh or not rec:
        return True
    status = rec.get("status")
    if status == "ok":
        return False
    if status == "pending" or not rec.get("checked_utc"):
        return True
    wait = ERROR_RETRY_DAYS if status == "error" else RETRY_DAYS
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(rec["checked_utc"])
    return age.days >= wait


def run(refresh: bool = False, limit: int | None = None) -> int:
    stations = station_ids()
    doc = load_cache()
    sites = doc["sites"]
    todo = [sid for sid in sorted(stations) if needs_lookup(sites.get(sid), refresh)]
    if limit:
        todo = todo[:limit]
    log.info("%d stations on the page, %d to look up", len(stations), len(todo))

    # 1) stream reach of each gage (USGS NLDI); reaches found on earlier runs are reused
    comid_of, failures = {}, 0
    for n, sid in enumerate(todo, 1):
        old = sites.get(sid) or {}
        if old.get("comid") and not refresh:
            comid_of[sid] = old["comid"]
            continue
        try:
            comid = find_comid(sid)
            failures = 0
        except Exception as exc:  # network trouble: try again tomorrow
            sites[sid] = {"status": "error", "error": f"NLDI: {str(exc)[:180]}", "checked_utc": _now()}
            failures += 1
            if failures >= 25:
                log.error("The NLDI service is not answering; stopping and keeping what we have.")
                break
            continue
        if comid is None:
            sites[sid] = {"status": "not indexed", "checked_utc": _now()}
        else:
            comid_of[sid] = comid
        if n % 100 == 0:
            log.info("  stream reaches: %d/%d", n, len(todo))
        time.sleep(PAUSE_S)

    # 2) watershed metrics of each reach (EPA StreamCat), in batches
    by_comid: dict[str, list[str]] = {}
    for sid, c in comid_of.items():
        by_comid.setdefault(c, []).append(sid)
    comids = sorted(by_comid)
    bad_batches, shown = 0, False
    for b in range(0, len(comids), BATCH):
        batch = comids[b:b + BATCH]
        try:
            rows = streamcat(batch)
            bad_batches = 0
        except Exception as exc:
            bad_batches += 1
            for c in batch:
                for sid in by_comid[c]:
                    sites[sid] = {"status": "error", "comid": c, "error": f"StreamCat: {str(exc)[:180]}",
                                  "checked_utc": _now()}
            if bad_batches >= 3:
                log.error("StreamCat is not answering; stopping and keeping what we have.")
                break
            continue
        for c in batch:
            m = rows.get(c) or {}
            if m and not shown:
                log.info("first watershed (COMID %s): %s", c, ", ".join(f"{k}={v:g}" for k, v in sorted(m.items())))
                shown = True
            rec = {"comid": c, "checked_utc": _now()}
            if m:
                rec.update(status="ok", **summarize(m))
            else:
                rec["status"] = "no attributes"
            for sid in by_comid[c]:
                sites[sid] = dict(rec)
        log.info("  watersheds: %d/%d reaches", min(b + BATCH, len(comids)), len(comids))
        _save(doc)  # keep progress if the run is cut short

    doc["variables"] = VARIABLES
    doc["source"] = ("Stream reaches: USGS Network-Linked Data Index (NHDPlus Version 2.1). Watershed "
                     "metrics: EPA StreamCat (Hill and others, 2016). Land-use classes after Chen and "
                     "Husic (2026).")
    doc["updated_utc"] = _now()
    _save(doc)
    counts: dict = {}
    for sid in stations:
        rec = sites.get(sid) or {}
        key = rec.get("landuse") or rec.get("status") or "not looked up"
        counts[key] = counts.get(key, 0) + 1
    log.info("watershed facts for %d stations: %s", len(stations), counts)
    return 0


def _save(doc: dict) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":"), sort_keys=True, ensure_ascii=False) + "\n")
    tmp.replace(OUT_PATH)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refresh", action="store_true", help="look up every station again")
    ap.add_argument("--limit", type=int, help="look up at most this many stations (testing)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return run(refresh=args.refresh, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())

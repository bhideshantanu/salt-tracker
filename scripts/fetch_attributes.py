"""Watershed facts for every salt-tracker station, from the USGS NLDI.

What it does, in plain words
1. Lists every station on the page: the live data and every winter archive.
2. For stations not looked up yet, asks the USGS Network-Linked Data Index (NLDI) which
   NHDPlus stream reach the gage sits on, then reads that reach's attributes for the
   whole upstream watershed ("tot" attributes from Wieczorek and others, Select
   Attributes for NHDPlus Version 2.1 Reach Catchments and Modified Network Routed
   Upstream Watersheds, USGS ScienceBase).
3. Keeps a short list: land cover (latest National Land Cover Database year in the
   service), imperviousness, road and population density, baseflow index, area, slope,
   elevation, climate, soils and dams.
4. Classifies each watershed the way Chen and Husic (2026, Communications Earth &
   Environment) did: urban, agricultural or forested when that cover is more than half
   of the basin, otherwise mixed.
5. Saves everything to data/site-attributes.json. Later runs only look up new stations,
   and retry failed lookups after RETRY_DAYS.

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
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_PATH = DATA_DIR / "site-attributes.json"
NLDI = "https://api.water.usgs.gov/nldi"
RETRY_DAYS = 14
PAUSE_S = 0.05  # be polite to the service
FORMAT_VERSION = 1

# NLCD class codes grouped the way the page uses them
URBAN, FOREST, AG = ("21", "22", "23", "24"), ("41", "42", "43"), ("81", "82")
WATER, WETLAND = ("11",), ("90", "95")

log = logging.getLogger("attributes")
_session = requests.Session()
_session.headers["User-Agent"] = "salt-season-tracker (shantanubhide.com/salt-tracker)"


# ---------------------------------------------------------------- talking to the NLDI


def _get(url: str, params=None, tries: int = 3):
    for attempt in range(1, tries + 1):
        try:
            r = _session.get(url, params=params, timeout=45)
            if r.status_code == 404:
                return None  # not indexed: a normal answer, not an error
            r.raise_for_status()
            return r.json() if r.content else None
        except (requests.RequestException, ValueError) as exc:
            if attempt == tries:
                raise
            log.warning("%s failed (attempt %d/%d): %s", url, attempt, tries, exc)
            time.sleep(5 * attempt)


def find_comid(site_id: str) -> str | None:
    """NHDPlus reach (COMID) of a USGS gage, e.g. USGS-01646500 -> '22338561'."""
    doc = _get(f"{NLDI}/linked-data/nwissite/{site_id}")
    for feat in (doc or {}).get("features", []):
        comid = (feat.get("properties") or {}).get("comid")
        if comid not in (None, ""):
            return str(comid)
    return None


def upstream_attributes(comid: str) -> dict[str, float]:
    """All 'tot' (total upstream watershed) attributes of one reach, as id -> number."""
    doc = _get(f"{NLDI}/linked-data/comid/{comid}/tot") or {}
    out = {}
    for row in doc.get("characteristics", []):
        cid, val = row.get("characteristic_id"), row.get("characteristic_value")
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        if v <= -9998:  # the dataset's "no data" code
            continue
        out[str(cid).upper()] = v
    return out


def attribute_metadata() -> dict[str, dict]:
    """Descriptions and units of every 'tot' attribute (only used for the file's notes)."""
    try:
        doc = _get(f"{NLDI}/lookups/tot/characteristics") or {}
    except Exception as exc:
        log.warning("Attribute descriptions unavailable (%s)", exc)
        return {}
    rows = doc.get("characteristicMetadata") or doc.get("characteristics") or []
    return {str(r.get("characteristic_id", "")).upper(): r for r in rows if r.get("characteristic_id")}


# ---------------------------------------------------------------- choosing the variables


def _year(two_or_four: str) -> int:
    n = int(two_or_four)
    return n if n > 1000 else (2000 + n if n < 50 else 1900 + n)


def _latest(attrs: dict, pattern: str):
    """Value and year of the most recent attribute whose id matches pattern (one group = year)."""
    best = None
    for cid, v in attrs.items():
        m = re.fullmatch(pattern, cid)
        if m:
            y = _year(m.group(1))
            if best is None or y > best[1]:
                best = (cid, y, v)
    return best


def _first(attrs: dict, *ids):
    for cid in ids:
        if cid in attrs:
            return cid, attrs[cid]
    return None


def summarize(attrs: dict) -> tuple[dict, dict]:
    """Turn the long attribute list into the page's short list (missing ones are left out)."""
    out, src = {}, {}

    # land cover: the newest NLCD year that has the class codes
    years = {}
    for cid in attrs:
        m = re.fullmatch(r"TOT_NLCD(\d\d)_(\d\d)", cid)
        if m:
            years.setdefault(_year(m.group(1)), set()).add(m.group(2))
    usable = [y for y, codes in years.items() if set(URBAN + FOREST + AG) <= codes]
    if usable:
        y = max(usable)
        yy = f"{y % 100:02d}"
        pct = lambda codes: round(sum(attrs.get(f"TOT_NLCD{yy}_{c}", 0.0) for c in codes), 1)
        out.update(urban_pct=pct(URBAN), forest_pct=pct(FOREST), ag_pct=pct(AG),
                   water_pct=pct(WATER), wetland_pct=pct(WETLAND), nlcd_year=y)
        src["land cover"] = f"TOT_NLCD{yy}_*"

    for key, pattern in [("impervious_pct", r"TOT_IMPV(\d\d)"), ("pop_density", r"TOT_POPDENS(\d\d)"),
                         ("dams", r"TOT_NDAMS(\d{4})")]:
        hit = _latest(attrs, pattern)
        if hit:
            out[key] = round(hit[2], 2)
            out[key.split("_")[0] + "_year"] = hit[1]
            src[key] = hit[0]

    for key, ids in [("area_km2", ("TOT_BASIN_AREA",)), ("bfi_pct", ("TOT_BFI",)),
                     ("road_density", ("TOT_TOTAL_ROAD_DENS", "TOT_ROAD_DENS")),
                     ("slope_pct", ("TOT_BASIN_SLOPE",)), ("elev_m", ("TOT_ELEV_MEAN",)),
                     ("clay_pct", ("TOT_CLAYAVE",)), ("sand_pct", ("TOT_SANDAVE",)),
                     ("snow_pct", ("TOT_PRSNOW",))]:
        hit = _first(attrs, *ids)
        if hit:
            out[key] = round(hit[1], 2)
            src[key] = hit[0]

    for key, pattern in [("precip_mm", r"TOT_PPT(\d{4})_ANN"), ("temp_c", r"TOT_TAV(\d{4})_ANN")]:
        hit = _latest(attrs, pattern)  # climate normals; "7100" sorts as the 1971-2000 period
        if hit:
            out[key] = round(hit[2], 1)
            src[key] = hit[0]

    out["landuse"] = classify(out)
    return out, src


def classify(a: dict) -> str | None:
    """Chen and Husic (2026): a class covering more than half of the basin, else mixed."""
    if "urban_pct" not in a:
        return None
    for key, name in (("urban_pct", "urban"), ("ag_pct", "agricultural"), ("forest_pct", "forested")):
        if a[key] > 50:
            return name
    return "mixed"


# ---------------------------------------------------------------- the stations


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
        if doc.get("version") == FORMAT_VERSION:
            return doc
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": FORMAT_VERSION, "sites": {}}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def needs_lookup(rec: dict | None, refresh: bool) -> bool:
    if refresh or not rec:
        return True
    if rec.get("status") == "ok":
        return False
    checked = rec.get("checked_utc")
    if not checked:
        return True
    return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(checked)).days >= RETRY_DAYS


def run(refresh: bool = False, limit: int | None = None) -> int:
    stations = station_ids()
    doc = load_cache()
    sites = doc.setdefault("sites", {})
    todo = [sid for sid in sorted(stations) if needs_lookup(sites.get(sid), refresh)]
    if limit:
        todo = todo[:limit]
    log.info("%d stations on the page, %d to look up", len(stations), len(todo))
    sources_used: dict[str, set] = {}
    failures = 0
    for n, sid in enumerate(todo, 1):
        rec = {"checked_utc": _now()}
        try:
            comid = find_comid(sid)
            if comid is None:
                rec["status"] = "not indexed"
            else:
                attrs = upstream_attributes(comid)
                if not sources_used and attrs:  # once per run, for the log
                    log.info("first station %s: %d attributes, e.g. %s", sid, len(attrs),
                             ", ".join(sorted(attrs)[:8]))
                summary, src = summarize(attrs)
                rec.update(status="ok" if attrs else "no attributes", comid=comid, **summary)
                for k, v in src.items():
                    sources_used.setdefault(k, set()).add(v)
        except Exception as exc:  # network trouble: try again on a later run
            rec.update(status="error", error=str(exc)[:200])
            failures += 1
            if failures >= 25 and failures == n:
                log.error("The NLDI service is not answering; stopping and keeping what we have.")
                break
        sites[sid] = rec
        if n % 100 == 0:
            log.info("  %d/%d done", n, len(todo))
            _save(doc)  # keep progress if the run is cut short
        time.sleep(PAUSE_S)

    if sources_used:  # descriptions only matter when something was looked up
        meta = attribute_metadata()
        notes = doc.setdefault("variables", {})
        for key, cids in sources_used.items():
            cid = sorted(cids)[-1]
            if "*" in cid:
                notes[key] = {"nldi_id": cid, "units": "percent of watershed",
                              "description": "National Land Cover Database classes, grouped: urban "
                                             "21-24, forest 41-43, agriculture 81-82, water 11, "
                                             "wetland 90 and 95"}
                continue
            m = meta.get(cid, {})
            notes[key] = {"nldi_id": cid,
                          "description": m.get("characteristic_description", ""),
                          "units": m.get("units", "")}
    doc["source"] = ("USGS NLDI 'tot' attributes (Wieczorek and others, Select Attributes for "
                     "NHDPlus Version 2.1 Reach Catchments and Modified Network Routed Upstream "
                     "Watersheds); land-use classes after Chen and Husic (2026)")
    doc["updated_utc"] = _now()
    _save(doc)
    ok = sum(1 for sid in stations if (sites.get(sid) or {}).get("status") == "ok")
    classes = {}
    for sid in stations:
        c = (sites.get(sid) or {}).get("landuse")
        classes[c] = classes.get(c, 0) + 1
    log.info("watershed facts for %d of %d stations; classes: %s", ok, len(stations), classes)
    return 0


def _save(doc: dict) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":"), sort_keys=True) + "\n")
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

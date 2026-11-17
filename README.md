# Salt Season Tracker

[![DOI](https://zenodo.org/badge/1446260456.svg)](https://doi.org/10.5281/zenodo.24316859)

A live map of road-salt pulses in U.S. streams. Every morning it downloads daily stream
salinity from U.S. Geological Survey (USGS) sensors and shows how each stream compares
with its own summer level, with past winters back to 2016–17 to replay and compare.

**Live page: https://shantanubhide.com/salt-tracker/**

## How to cite

If you use the tracker or its data, please cite it:

> Bhide, S. V. (2026). *Salt Season Tracker: a live map of road-salt pulses in U.S.
> streams* (Version 1.0.0) [Computer software]. Zenodo.
> https://doi.org/10.5281/zenodo.24316860

That DOI is for version 1.0.0 exactly, code and data as of its release; please use it
(or the DOI of a later version) in papers. To refer to the tracker in general, use
https://doi.org/10.5281/zenodo.24316859, which always points to the newest version.

GitHub's **Cite this repository** button (right side of this page) gives the same
citation in APA and BibTeX, from [`CITATION.cff`](CITATION.cff). What changed in each
version is in [`CHANGELOG.md`](CHANGELOG.md).

## What's on the page

- **Season picker:** this season (live) or a past winter, such as Winter 2025–26, to
  replay day by day with **Play**.
- **Area picker and zoom:** pick a state (or Washington, DC) to zoom in. The headline,
  tiles, chart, storms and rankings switch to that state, with its national rank. Drag to
  pan; zoom with + and −, a pinch, or Ctrl/⌘ + scroll.
- **Watershed land use:** each station's upstream watershed is classed as urban,
  agricultural, forested or mixed (Chen and Husic, 2026). Pick a class to show only those
  streams; everything on the page follows it. The *Season: land use* tab compares the
  four classes side by side. Hover details and the stream chart show the watershed's land
  cover, imperviousness, road and population density, baseflow index and area.
- **Four map views:** *Today* (each stream vs. its summer level), *Season peak*, *Days at
  3×+*, and *Between storms* (the low end of a stream's winter readings: salt that stays
  after the pulses pass).
- **Salt storms:** detected automatically (many sensors in one region jumping to 3× within
  a day or two). The three biggest are labeled on the chart; click one in the list to
  jump to that day.
- **Flush or low-flow concentration:** for salty readings at sensors that also measure
  streamflow, whether flow was above typical (runoff washing salt in) or below it (less
  dilution, or salty groundwater).
- **Rankings:** saltiest streams today, a season scorecard for every stream (peak, peak
  date, days at 1.5×/3×/10×, days back under 1.5× after a pulse, between-storm level, flow
  at peak, watershed land use, imperviousness, baseflow index), for every state and for
  each land-use class. Sortable, clickable, downloadable as CSV. The stream CSVs also
  carry every watershed variable, ready for analysis.
- **Stream chart:** hover a dot for a quick summary; click it (tap twice on a phone) to
  open a larger chart under the map: daily salinity against the stream's summer level
  with 3× and 10× lines, and streamflow against its typical flow, over the whole data
  window. Hover the chart to read any day; click it to show that day on the map. The panel
  also has the stream's season numbers, a daily table, a one-stream CSV and a link to the
  USGS page. Clicking a stream in the rankings opens it too; Esc or Close shuts it.
- **Save image:** a 2400 × 2400 PNG of the current season, view, state and zoom.
- Links like `?season=2025-26&state=PA&view=peak` open the page in that state;
  add `&landuse=urban` for one land-use class, or `&site=USGS-01646500` to open one
  stream's chart.

## Using the data

Everything the page shows is in this repository and free to reuse (see License below).

- **On the page:** every ranking table and every stream's chart has a **Download CSV**
  button. The stream CSVs include each station's watershed variables.
- **Files in [`data/`](data/)**, updated every morning:
  - `salt.json`: the live data, the last 185 days of daily specific conductance and
    streamflow for every sensor, with each stream's summer (May–October) level.
  - `season-YYYY-YY.json`: one archive per past winter, from `season-2016-17.json` on;
    `seasons.json` lists them.
  - `site-attributes.json`: each station's watershed facts and land-use class, keyed by
    USGS site ID, with the units of every variable.
  - `excluded-sites.json`: the coastal sensors left out, with the reason for each.
- **A fixed copy for a paper:** cite a numbered release (see How to cite). Each release
  is a frozen snapshot of the code and the data on that day.

## How it works

Everything runs on GitHub's servers: a scheduled job downloads the USGS data every
morning, works out the numbers, and saves them as files that the web page reads.

| Part | File | What it does |
|---|---|---|
| Data script | `scripts/fetch_salt.py` | Downloads daily mean specific conductance (USGS parameter 00095) for every stream sensor in the lower 48 states, works out each stream's May–October median ("normal"), downloads daily streamflow (00060) at the same sensors, and saves the last 185 days to `data/salt.json`. It also archives each finished winter (November–April) every May. |
| Data files | `data/salt.json`, `data/season-YYYY-YY.json`, `data/seasons.json`, `data/site-attributes.json`, `data/excluded-sites.json` | The live data, one archive per past winter, the list of archives for the season picker, each station's watershed facts, and the coastal sensors left out (with reasons). |
| Watershed script | `scripts/fetch_attributes.py` | Matches each station to its NHDPlus stream segment with the USGS Network-Linked Data Index (NLDI), reads that segment's whole-watershed metrics from EPA StreamCat, and saves land cover, the land-use class and other basin variables to `data/site-attributes.json`. Runs after the salt update; only new stations are looked up, so it takes seconds on a normal day. |
| Coastal filter inputs | `sources/coastline-us.json`, `sources/coastal-sites.csv` | The U.S. ocean coastline (Natural Earth, public domain) and a short manual keep/drop list. |
| Daily robot | `.github/workflows/update-data.yml` | A GitHub Actions workflow. GitHub runs the data script for free every morning at 13:37 UTC and saves the new files in `data/` back into this repository. |
| Backfill robot | `.github/workflows/backfill-winters.yml` | Run by hand when needed. Builds every missing winter archive back to a chosen year, one winter at a time, saving each as it finishes. |
| Web page | `index.html` | Everything visitors see. GitHub Pages hosts it. |

`assets/states.json` holds the state outlines (built once by `scripts/build_states.py`).
`scripts/albers.py` is the map projection both the outlines and the sensors use.

## Method notes and limits

- **Measure:** specific conductance, µS/cm at 25 °C, daily mean (statistic 00003).
- **Normal level:** median of the stream's May–October daily means within the last 400
  days. A stream needs at least 60 summer days and a normal under 3,000 µS/cm.
- **Sites:** USGS sites typed as "Stream" in the lower 48 only. Tidal streams, estuaries,
  lakes and wells are left out.
- **Coastal filter:** many tidal reaches are still typed "Stream", and their salty readings
  are seawater, not road salt. A sensor is left out when USGS also records salinity,
  tidally filtered streamflow or water level against sea level at the gage, or when the
  gage is below 15 ft elevation and within 25 km of the ocean or Gulf coastline. Every
  sensor left out is listed with its reason in `data/excluded-sites.json` and on the page.
- **Manual list:** a few sensors are kept or left out by hand, each with a reason, in
  `sources/coastal-sites.csv`.
- **Categories:** under 1.5×, 1.5–3×, 3–10×, 10× or more of normal.
- **Season:** metrics run from November 1 to the selected day.
- **Between storms:** 25th percentile of a stream's daily ratios over the season (14+
  readings needed).
- **Days back under 1.5×:** from the peak of a 3×+ pulse to the first reading below 1.5×;
  the median across a stream's pulses. If another storm pushes the stream back to 3× before
  it drops below 1.5×, the count restarts from that newer peak.
- **Salt storm:** a sensor spikes when it reaches 3× after two days below 3×. A storm day
  needs at least 8 spikes (and 3% of reporting sensors) in one region; nearby days and
  regions are merged.
- **Flow:** daily mean discharge at the same gage. "Typical" is the median daily flow over
  the period shown. Many sensors have no flow gage, and ice can interrupt winter records.
- **Watersheds:** each USGS site is matched to its NHDPlus Version 2.1 stream segment by
  the USGS NLDI. The whole watershed upstream of that segment is described with EPA's
  [StreamCat](https://www.epa.gov/national-aquatic-resource-surveys/streamcat-dataset)
  dataset (Hill and others, 2016): NLCD 2019 land cover and imperviousness, 2010 census
  population density, road density, baseflow index, PRISM 1981–2010 climate normals, soils
  and dam density. Developed land is NLCD 21–24, forest 41–43 and agriculture 81–82
  (pasture/hay and crops). A watershed is urban, agricultural or forested when that cover
  is more than half of it, otherwise mixed, after
  [Chen and Husic (2026)](https://doi.org/10.1038/s43247-026-03788-2). Stations the NLDI
  doesn't match (often small or recently added gages) show no watershed facts; they are
  retried after 14 days, and failed lookups the next day. (The NLDI used to serve these
  attributes itself; USGS removed that part of the service in 2026.)
- **Caveats:** data are provisional. Drought, wastewater, mining and sensor problems can
  also raise conductance. Conductance is not chloride concentration.

## Data sources and credits

- Stream salinity and flow: [USGS Water Data API](https://api.waterdata.usgs.gov/), via the
  USGS [`dataretrieval`](https://github.com/DOI-USGS/dataretrieval-python) package.
  Provisional data.
- Stream segments: [USGS Network-Linked Data Index](https://api.water.usgs.gov/nldi/)
  (NHDPlus Version 2.1).
- Watershed metrics: EPA
  [StreamCat](https://www.epa.gov/national-aquatic-resource-surveys/streamcat-dataset)
  (Hill and others, 2016).
- Land-use classes: [Chen and Husic (2026)](https://doi.org/10.1038/s43247-026-03788-2),
  *Communications Earth & Environment*.
- Coastline and the District of Columbia outline: Natural Earth (public domain). State
  outlines: U.S. Census data via the folium project's `us-states.json`.

## License

Code: [MIT License](LICENSE), © 2026 Shantanu V. Bhide. The data the tracker downloads
come from U.S. government sources (USGS, EPA) and Natural Earth (public domain); please
also credit them, and Chen and Husic (2026) for the land-use classes, when you use the
watershed data.

Running your own copy, or maintaining this one: see [`MAINTAINING.md`](MAINTAINING.md).

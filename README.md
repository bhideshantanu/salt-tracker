# Salt Season Tracker

A live map of road-salt pulses in U.S. streams. Every morning it downloads daily stream
salinity from USGS sensors and shows how each stream compares with its own summer level.

Live page (once set up): **https://shantanubhide.com/salt-tracker/**

## What's on the page

- **Season picker:** this season (live) or a past winter, such as Winter 2025–26, to
  replay day by day with **Play**.
- **Area picker and zoom:** pick a state to zoom in. The headline, tiles, chart, storms
  and rankings switch to that state, with its national rank. Drag to pan; zoom with + and
  −, a pinch, or Ctrl/⌘ + scroll.
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
  at peak) and for every state. Sortable, clickable, downloadable as CSV.
- **Save image:** a 2400 × 2400 PNG of the current season, view, state and zoom.
- Links like `?season=2025-26&state=PA&view=peak` open the page in that state.

## How it works

There are three parts, and you never have to run anything on your own computer.

| Part | File | What it does |
|---|---|---|
| Data script | `scripts/fetch_salt.py` | Downloads daily mean specific conductance (USGS parameter 00095) for every stream sensor in the lower 48 states, works out each stream's May–October median ("normal"), downloads daily streamflow (00060) at the same sensors, and saves the last 185 days to `data/salt.json`. It also archives each finished winter (see below). |
| Data files | `data/salt.json`, `data/season-YYYY-YY.json`, `data/seasons.json`, `data/excluded-sites.json` | The live data, one archive per past winter, the list of archives for the season picker, and the coastal sensors left out (with reasons). |
| Coastal filter inputs | `sources/coastline-us.json`, `sources/coastal-sites.csv` | The U.S. ocean coastline (Natural Earth, public domain) and a short manual keep/drop list. |
| Daily robot | `.github/workflows/update-data.yml` | A GitHub Actions workflow. GitHub runs the data script for free every morning at 13:37 UTC and saves the new files in `data/` back into this repository. |
| Web page | `index.html` | Everything visitors see. GitHub Pages hosts it. |

`assets/states.json` holds the state outlines (built once by `scripts/build_states.py`).
`scripts/albers.py` is the map projection both the outlines and the sensors use.

### What a daily run does

- **Daily update (fast):** re-downloads only the last 21 days and merges them in.
- **Full rebuild:** downloads 400 days and recalculates each stream's normal level. It
  happens automatically on the first run, after an upgrade that changes the file layout,
  and whenever the normal levels are more than 30 days old. You can also force one from
  the Actions tab.
- **Winter archive:** when the most recent finished winter (November–April) has no
  archive yet, the run builds one: that winter's daily values, compared with the
  May–October before it, plus streamflow. This happens once a year, in early May (and on
  the first run after this upgrade, for winter 2025–26). If the archive download fails,
  the live data is still saved and the next morning's run tries again.

### Adding an older winter (optional)

**Actions → Update salt data → Run workflow**, type the winter's starting year in the
season box (for example `2024` for winter 2024–25), and run it. It adds
`data/season-2024-25.json` and the page's season picker lists it. Older winters have
fewer sensors.

## One-time setup (about 15 minutes)

### 1. Create the repository

1. On GitHub, click **+ → New repository**.
2. Name it `salt-tracker`, set it to **Public**, and click **Create repository**.
3. Upload these files: on the new repo page click **uploading an existing file**, drag in
   everything from this folder (including the hidden `.github` folder and `.nojekyll`),
   and click **Commit changes**.
   - On a Mac, press `Cmd + Shift + .` in Finder to show hidden files before dragging.
   - Check afterwards that `.github/workflows/update-data.yml` is in the repo. If the
     upload skipped it, use **Add file → Create new file**, type
     `.github/workflows/update-data.yml` as the name, and paste the file's contents.

### 2. Add a free USGS API key (recommended)

The USGS limits how many requests you can make per hour without a key.

1. Sign up at https://api.waterdata.usgs.gov/signup/ (the key arrives by email).
2. In your repo: **Settings → Secrets and variables → Actions → New repository secret**.
3. Name: `API_USGS_PAT`. Secret: paste the key. Click **Add secret**.

### 3. Turn on the website

1. **Settings → Pages**.
2. Under **Build and deployment**, set **Source** to **Deploy from a branch**, branch
   **main**, folder **/ (root)**, then **Save**.
3. Because `bhideshantanu.github.io` already uses `shantanubhide.com`, this repo is
   served at **https://shantanubhide.com/salt-tracker/** automatically.

### 4. Run it the first time

1. Open the **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead
   and enable them**.
2. Click **Update salt data → Run workflow**, tick **Rebuild everything**, and click the
   green **Run workflow** button.
3. Wait for the green check (the first full run takes several minutes). Click into it to
   see the log if anything goes red.
4. Open https://shantanubhide.com/salt-tracker/ (give Pages a minute or two to update).

After this, it updates itself every morning.

## Posting to LinkedIn

1. Open the page, drag the slider (or press **Play**) to the day you want.
2. Pick the season, area and map view you want; zoom in if it helps.
3. Click **Save image**. You get a 2400 × 2400 PNG with the headline, map, legend, a
   season strip and your site name.
4. Post the image itself (not just the link), write two or three lines about what it
   shows, and put the page link in the first comment.

Best moments to post: the morning after the first big snowstorm of the season, and an
end-of-season recap in March.

## Previewing on your own computer (optional)

You need Python 3.10 or newer.

```bash
cd salt-tracker
python scripts/fetch_salt.py --demo   # writes SYNTHETIC test data (clearly labeled)
python -m http.server 8000            # then open http://localhost:8000
```

Opening `index.html` by double-clicking won't work: browsers block pages opened from a
file from reading other files, so it needs the small local server above.

To try real data locally: `pip install -r requirements.txt`, then
`python scripts/fetch_salt.py --full`. Before uploading to GitHub, delete
`data/salt.json` if it holds demo data. The robot replaces it anyway, but the page shows
a demo banner until it does.

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
- **Overriding the filter:** add a line to `sources/coastal-sites.csv`, such as
  `USGS-01649500,keep,urban stream above the head of tide` (or `drop`). The next morning's
  run rebuilds the live data and last winter's archive with the change.
- **Categories:** under 1.5×, 1.5–3×, 3–10×, 10× or more of normal.
- **Season:** metrics run from November 1 to the selected day.
- **Between storms:** 25th percentile of a stream's daily ratios over the season (14+
  readings needed).
- **Days back under 1.5×:** from the peak of a 3×+ pulse to the first reading below 1.5×;
  the median across a stream's pulses.
- **Salt storm:** a sensor spikes when it reaches 3× after two days below 3×. A storm day
  needs at least 8 spikes (and 3% of reporting sensors) in one region; nearby days and
  regions are merged.
- **Flow:** daily mean discharge at the same gage. "Typical" is the median daily flow over
  the period shown. Many sensors have no flow gage, and ice can interrupt winter records.
- **Caveats:** data are provisional. Drought, wastewater, mining and sensor problems can
  also raise conductance. Conductance is not chloride concentration.

## If something goes wrong

- **Red X on the Actions tab:** open the run and read the last lines of the log.
  - `429` or "rate limit": add the API key (step 2) and re-run.
  - `No data downloaded from USGS`: the USGS service may be down; try again later.
- **Page says "The data didn't load":** the workflow hasn't produced `data/salt.json`
  yet. Run it (step 4).
- **No season picker:** the winter archive hasn't been built yet. The log of the latest
  run says why ("Could not build the ... archive this time"); the next run retries.
- **Run takes long:** the first run after an upgrade does a full rebuild plus last
  winter's archive, which can take 30–60 minutes. Daily runs take a few minutes.
- **The workflow stopped running:** GitHub pauses scheduled workflows in repos with no
  activity for 60 days. The daily data commits normally count as activity; if it pauses,
  re-enable it on the Actions tab.

Data: [USGS Water Data API](https://api.waterdata.usgs.gov/) via the USGS
[`dataretrieval`](https://github.com/DOI-USGS/dataretrieval-python) package. State
outlines from U.S. Census data via the folium project's `us-states.json`.

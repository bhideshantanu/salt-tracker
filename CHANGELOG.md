# Changelog

All notable changes to the Salt Season Tracker. The data files in `data/` are updated
every morning by the daily robot; those updates are not listed here. Version numbers:
the last number goes up for fixes, the middle number for new features, and the first
number when a change to the methods or the data files changes the numbers people see.

## 1.0.0 — 2026-11-17

First citable release, archived on Zenodo with a DOI.

### Added
- MIT license (`LICENSE`), citation file (`CITATION.cff`) for GitHub's "Cite this
  repository" button and Zenodo, and this changelog.
- The README is now written for visitors (what the page shows, using the data, methods,
  how to cite); setup, maintenance and release steps moved to `MAINTAINING.md`.

### What 1.0.0 contains
- A live map of daily specific conductance at USGS stream sensors in the lower 48
  states, each compared with its own May–October median, updated every morning by
  GitHub Actions.
- Four map views (Today, Season peak, Days at 3×+, Between storms), a state picker with
  zoom (including Washington, DC), automatic salt-storm detection, and the flush vs.
  low-flow split at sensors that also measure streamflow.
- Rankings: saltiest streams today and season scorecards for every stream, every state
  and each watershed land-use class, all downloadable as CSV.
- A chart for each stream: daily salinity against its summer level and streamflow
  against its typical flow, with the stream's season numbers and watershed facts.
- Winter archives from 2016–17 to 2025–26, replayable day by day.
- Watershed facts for each station: its NHDPlus Version 2.1 stream segment from the
  USGS Network-Linked Data Index, whole-watershed metrics from EPA StreamCat (NLCD 2019
  land cover and imperviousness, 2010 census population density, road density,
  baseflow index, PRISM 1981–2010 climate normals, soils, dam density), and a land-use
  class after Chen and Husic (2026).
- A filter that leaves out tidal and low-lying coastal sensors, where seawater rather
  than road salt drives salinity, with a published list of the sensors left out.

## Before 1.0.0

Development milestones before the first numbered release.

### 2026-11-13
- Washington, DC can be picked and zoomed to (outline added from Natural Earth).
- Watershed land use and basin variables for every station, a Watershed filter, a
  *Season: land use* scorecard tab, and watershed columns in the CSV downloads.
- Backfill workflow; winters 2016–17 to 2024–25 archived.
- Fixed: the method note now says that "days back under 1.5×" restarts from a newer
  peak when another storm pushes a stream back to 3× before it recovers; typical flow
  is rounded exactly; the watershed lookup's `requests` dependency is listed in
  `requirements.txt`; watershed metrics come from EPA StreamCat after USGS removed the
  NLDI's own watershed-attribute endpoints.

### 2026-11-06
- Stream chart panel: click a stream for its salinity and streamflow chart, season
  numbers, daily table, one-stream CSV and USGS link.

### 2026-11-05
- Replay of last winter (2025–26), state picker and zoom, season scorecards, more map
  views, "salt memory" metrics (days back under 1.5×, between-storm level), salt-storm
  detector and flow context.
- Filter for tidal and low-lying coastal sensors, with a review list and a manual
  keep/drop file (`sources/coastal-sites.csv`).

### 2026-11-04
- Launch: daily map of U.S. stream salinity against each stream's summer level, updated
  every morning by GitHub Actions.

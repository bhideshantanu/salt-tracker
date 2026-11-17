# Running and maintaining the Salt Season Tracker

Notes for whoever maintains this repository, or wants to run their own copy. Visitors
only need the [README](README.md).

## What a daily run does

- **Daily update (fast):** re-downloads only the last 21 days and merges them in.
- **Full rebuild:** downloads 400 days and recalculates each stream's normal level. It
  happens automatically on the first run, after an upgrade that changes the file layout,
  and whenever the normal levels are more than 30 days old. You can also force one from
  the Actions tab.
- **Winter archive:** when the most recent finished winter (November–April) has no
  archive yet, the run builds one: that winter's daily values, compared with the
  May–October before it, plus streamflow. This happens once a year, in early May. If
  the archive download fails, the live data is still saved and the next morning's run
  tries again.

## Setting up your own copy (about 15 minutes)

Nothing needs to run on your own computer.

### 1. Copy the repository

Click **Fork** at the top of this repository (or create a new public repository and
upload these files, including the hidden `.github` folder; on a Mac, press
`Cmd + Shift + .` in Finder to show hidden files). In a fork, GitHub keeps workflows off
until you turn them on: open the **Actions** tab and enable them.

### 2. Add a free USGS API key (recommended)

The USGS limits how many requests you can make per hour without a key.

1. Sign up at https://api.waterdata.usgs.gov/signup/ (the key arrives by email).
2. In your repository: **Settings → Secrets and variables → Actions → New repository
   secret**.
3. Name: `API_USGS_PAT`. Secret: paste the key. Click **Add secret**.

### 3. Turn on the website

1. **Settings → Pages**.
2. Under **Build and deployment**, set **Source** to **Deploy from a branch**, branch
   **main**, folder **/ (root)**, then **Save**.
3. The page appears at `https://<your-username>.github.io/salt-tracker/`, or under your
   own domain if your GitHub user site has one.

### 4. Run it the first time

1. **Actions → Update salt data → Run workflow**, tick **Rebuild everything**, and click
   the green **Run workflow** button.
2. Wait for the green check (a full run takes several minutes). Click into it to see the
   log if anything goes red.
3. Open your page (give Pages a minute or two to update).

After this, it updates itself every morning.

## Adding older winters

**All of them at once:** **Actions → Backfill past winters → Run workflow**, keep `2016`
(or type the first winter you want) and run it. It works backward from the most recent
winter, skips winters that already have an archive, and saves each winter as soon as it
finishes, so a run that stops partway keeps what it built; run it again to pick up the
rest. A winter can take half an hour or more, so the whole set may take a few hours
(GitHub stops a run after 6 hours). Older winters have fewer sensors.

**Just one:** **Actions → Update salt data → Run workflow**, type the winter's starting
year in the season box (for example `2024` for winter 2024–25), and run it. It adds
`data/season-2024-25.json` and the page's season picker lists it.

## Overriding the coastal filter

Add a line to `sources/coastal-sites.csv`, such as
`USGS-01649500,keep,urban stream above the head of tide` (or `drop`). The next morning's
run rebuilds the live data and last winter's archive with the change.

## Releasing a new version

Release when the code or methods change in a way worth citing, and once each spring
after a winter's archive is built. Never release for the daily data updates alone: each
release creates a new DOI.

1. Add a section for the new version at the top of `CHANGELOG.md`.
2. In `CITATION.cff`, update `version` and `date-released`, and in the README's How to
   cite, update the version number.
3. On GitHub: **Releases → Draft a new release → Choose a tag**, type the new tag (for
   example `v1.1.0`), target `main`, paste the changelog section as the description, and
   click **Publish release**. Zenodo archives it and creates a DOI for that version.

Version numbers: the last number goes up for fixes, the middle number for new features,
and the first number when a change to the methods or the data files changes the numbers
people see.

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

## If something goes wrong

- **Red X on the Actions tab:** open the run and read the last lines of the log.
  - `429` or "rate limit": add the API key (setup step 2) and re-run.
  - `No data downloaded from USGS`: the USGS service may be down; try again later.
- **Page says "The data didn't load":** the workflow hasn't produced `data/salt.json`
  yet. Run it (setup step 4).
- **No season picker:** the winter archive hasn't been built yet. The log of the latest
  run says why ("Could not build the ... archive this time"); the next run retries.
- **Run takes long:** the first run after an upgrade does a full rebuild plus last
  winter's archive, which can take 30–60 minutes. Daily runs take a few minutes.
- **No watershed picker:** `data/site-attributes.json` doesn't exist yet or holds no
  land-use classes. Open the latest run's **Look up watershed facts** step: it lists
  how many stations were matched and, for the first one, which StreamCat values came
  back. This step never stops the salt update.
- **The workflow stopped running:** GitHub pauses scheduled workflows in repos with no
  activity for 60 days. The daily data commits normally count as activity; if it pauses,
  re-enable it on the Actions tab.

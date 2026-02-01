# Professional CPD Planner

Every day there is new knowledge and the world is advancing quickly. The same is true for professionals in real estate, construction, and the built environment who need to keep their skills up-to-date through CPD (Continuing Professional Development).

In reality, busy schedules make it easy to miss email reminders and valuable CPD events. This project turns CPD listings into an interactive dashboard to help you easily plan, record, and manage your CPD journey. It also includes a one-tap shortcut to add CPD event to your smartphone calendar directly with minimal friction.

Sample dashboard: https://cpd-planner.pages.dev/

## What this project does

- Fetches event data from a public website and normalizes it into a structured dataset
- Tags each event by major topic categories for fast discovery
- Renders a simple dashboard with filters and a list view
- Generates one-tap calendar files (`.ics`) for smartphones

## How it works (framework overview)

1) **Data fetcher**
   - `scraper/monitor_cpd.py` pulls listing pages and event detail pages.
   - The listing parser collects event IDs and status information.
   - The detail parser extracts event fields like title, date, fee, and venue.

2) **Normalizer**
   - Dates and times are parsed into standard formats.
   - Fees are converted into simple buckets (Free / Paid / Others).
   - Events are filtered to include only those on or after the current date.

3) **Categorizer**
   - `docs/taxonomy.json` defines topic groups and keywords.
   - Each event is matched to one or more categories based on its text content.

4) **Dashboard**
   - `docs/index.html` loads `docs/data.json` and renders the UI.
   - Users filter by division, fee, and topic to find relevant events quickly.
   - A one-tap action exports `.ics` files for smartphone calendars.

## Key tools and mechanisms

- **Python + requests + BeautifulSoup** for data fetching and parsing
- **JSON** for structured data output
- **Static HTML/CSS/JS** for a fast, portable dashboard
- **ICS export** for calendar integration

## Project structure

- `config/site.json` – site configuration (URLs, selectors, labels, timezone)
- `scraper/monitor_cpd.py` – fetches, parses, and writes `docs/data.json`
- `docs/index.html` – dashboard UI
- `docs/data.json` – generated dataset (this is what the UI reads)
- `docs/taxonomy.json` – categories and keywords for tagging

## Configure for another website

1) Update `config/site.json`
   - `listing_url_template` and `detail_url_template`
   - `id_regex` for extracting event IDs
   - `listing_*_selector` for finding rows, links, and status text
   - `detail_labels` in the order they appear on the detail page
   - `timezone_offset_hours` for correct local dates

2) Adjust `docs/taxonomy.json`
   - Add keywords that match the wording used in your target website

3) If the target site does not use label/value blocks
   - Update the parsing logic in `scraper/monitor_cpd.py` to match the page structure

## Run locally

Install dependencies:

```
python -m pip install -r scraper/requirements.txt
```

Generate the dataset:

```
python scraper/monitor_cpd.py
```

Serve the dashboard:

```
python -m http.server --directory docs 8000
```

Open the local server address shown in your terminal.

## Deploy and refresh automatically

You can deploy the `docs/` folder to any static hosting service. To keep the data fresh, schedule a daily job to run `scraper/monitor_cpd.py` and commit the updated `docs/data.json` back to your repository. The included workflow file is a ready-to-use example if your hosting setup supports scheduled jobs.

# SEM Performance Dashboard — Dashboard Specification

> **Purpose:** This document is the single source of truth for rebuilding, understanding, or extending the WoW & Geo SEM Performance Dashboard. Paste it to an AI agent and it can fully reconstruct the dashboard or answer any question about how it works.

> **Last updated:** 2026-08-26

---

## 1. Overview

A static HTML dashboard (GitHub Pages) tracking week-over-week SEM performance for monday.com across all Google Ads Search campaigns, organized by **cluster** (product/geo/agent groupings) and by **geo** (country-level breakdowns with brand/non-brand split).

- **URL:** `https://nymeria-ai.github.io/monday-wow-dashboard/`
- **Repo:** `nymeria-ai/monday-wow-dashboard` (GitHub, `main` branch)
- **Stack:** Single `index.html` with inline CSS + vanilla JS + Chart.js 4.4.7 (CDN). No frameworks, no build step.
- **Data:** Embedded as JS objects (`DATA`, `GEO_DATA_BRAND`, `GEO_DATA_NONBRAND`) inside `<script>` tags. No external API calls at runtime.
- **Refresh script:** `refresh.py` (Python 3)

---

## 2. Data Source & API Access

### Google Ads Accounts

All data is pulled from **5 Google Ads accounts** under the monday.com MCC (`764-577-9471`):

| Account ID | Name | Notes |
|---|---|---|
| `3746504118` | Main | Core product campaigns |
| `6629846296` | Verticals | Vertical-specific campaigns |
| `9194503735` | Verticals2 | Overflow verticals |
| `9441310809` | Locals | Localized campaigns |
| `6073520942` | Brand | Brand campaigns (always → "Brand" cluster) |

### API Access

- All queries go through **Funnel Gate** (`http://localhost:9400/execute`) — a local proxy that handles OAuth tokens and audit logging.
- API action: `gaql_query` on platform `google_ads`.
- **Never call Google Ads API directly.** Funnel Gate manages auth tokens and audit trail.
- Channel type filter: `campaign.advertising_channel_type = 'SEARCH'` (Search only).

### Data Range

- **Start date:** `2026-06-01` (hardcoded in `refresh.py` as `START_DATE`)
- **End date:** Yesterday (dynamically computed at refresh time)

---

## 3. Week Definition

### ⚠️ Wednesday to Tuesday (NOT Monday–Sunday!)

**Reason:** VBB (Value-Based Bidding) conversion data has a **5-day reporting lag**. By using Wednesday–Tuesday weeks, the VBB conversions from Wednesday (start of week) have had 5+ full days to report by the time the week closes on Tuesday.

**Function:** `week_start_wed(date_str)` — converts any date to the Wednesday that starts its Wed–Tue week.

```python
# weekday(): Mon=0, Tue=1, Wed=2, …, Sun=6
# offset from Wednesday: (weekday - 2) % 7
wednesday = d - timedelta(days=(d.weekday() - 2) % 7)
```

---

## 4. Metrics — How Each One Is Pulled

### ╔══════════════════════════════════════════════════════════════╗
### ║  LOCKED METRIC DEFINITIONS — DO NOT CHANGE WITHOUT TAL'S OK ║
### ╚══════════════════════════════════════════════════════════════╝

All conversion metrics use `metrics.all_conversions` (secondary actions, includes cross-device and view-through). Verified by Tal Herman on 2026-07-30.

### 4.1 Performance Metrics

**GAQL Query:**
```sql
SELECT campaign.name, segments.date,
       metrics.cost_micros, metrics.impressions, metrics.clicks
FROM campaign
WHERE segments.date BETWEEN '{START_DATE}' AND '{end_date}'
  AND campaign.advertising_channel_type = 'SEARCH'
```

| Metric | Source Field | Transformation |
|---|---|---|
| **Spend** | `metrics.cost_micros` | Divided by 1,000,000 for USD |
| **Impressions** | `metrics.impressions` | Raw integer |
| **Clicks** | `metrics.clicks` | Raw integer |

### 4.2 Conversion Metrics

**GAQL Query:**
```sql
SELECT campaign.name, segments.date,
       segments.conversion_action_name, metrics.all_conversions, metrics.all_conversions_value
FROM campaign
WHERE segments.date BETWEEN '{START_DATE}' AND '{end_date}'
  AND campaign.advertising_channel_type = 'SEARCH'
  AND segments.conversion_action_name IN (
    'Hard Signup (MCC)',
    'Hard signup Work goal (MCC)',
    'Paying (MCC)',
    'Agent Created (MCC)',
    'VBB - HT prod - offline conversions'
  )
```

| Conversion Action Name | Field Used | Maps To | Notes |
|---|---|---|---|
| `Hard Signup (MCC)` | `metrics.all_conversions` | `signups` | ctID 402542787 |
| `Hard signup Work goal (MCC)` | `metrics.all_conversions` | `work_signups` | ctID 318041244 |
| `Paying (MCC)` | `metrics.all_conversions` | `payers` | ctID 241978033 |
| `Agent Created (MCC)` | `metrics.all_conversions` | `agents_created` | ctID 7638407984 |
| `VBB - HT prod - offline conversions` | `metrics.all_conversions_value` ⚠️ | `vbb_value` | ctID 7277286158. Uses **VALUE**, not count! |

**Critical:** VBB uses `all_conversions_value` (the dollar value), NOT `all_conversions` (the count). All other conversions use count.

### 4.3 Derived / Calculated Metrics

Computed in frontend JavaScript, not pulled from Google Ads:

| Metric | Formula | Notes |
|---|---|---|
| **CPS** (Cost Per Signup) | `spend / signups` | `—` if signups = 0 |
| **CAC** (Customer Acquisition Cost) | `spend / payers` | `—` if payers = 0 |
| **CPAC** (Cost Per Agent Created) | `spend / agents_created` | `—` if AC = 0 |
| **CTR** (Click-Through Rate) | `clicks / impressions` | Shown as `X.X%` |
| **CR** (Conversion Rate) | `signups / clicks` | Shown as `X.X%` |
| **%Work** (Work Signup %) | `work_signups / signups` | Indicates intent quality |
| **VBB ROAS** | `vbb_value / spend` | Return on ad spend from VBB |
| **WoW Δ** (Week-over-Week) | `(this_week - prev_week) / prev_week` | `▲ X.X%` or `▼ X.X%` |

---

## 5. Campaign Clustering

The `extract_cluster()` function maps each campaign name to a dashboard cluster. Processing order matters — first match wins.

### 5.1 Campaign Name Formats

**Long format (PRM):**
```
{geo}-{lang}-prm-{product}-{channel}-{cluster}-{match}-{device}-{theme}-{network}
```
- Position `[5]` (0-indexed, split by `-`) = cluster value
- Detected when `len(parts) >= 6` and `parts[2] == "prm"`

**Short format:**
```
{geo}-s-{cluster}-{match}-{device}-{variant}
```
- Position `[2]` = cluster value
- Detected when `len(parts) >= 3` and `parts[1] == "s"`

### 5.2 Processing Order

1. **Exclusions (first!)** — Skip if ANY part of the campaign name contains:
   - `crm`, `service`, `globster`, `elevate`, `taka`
   - Also skip if any part equals: `lead_management`, `account_management`, `lead_agent`

2. **Brand account** — Account `6073520942` → always **"Brand"**

3. **Brand/Comp keywords** — If any part:
   - equals `brand` or starts with `brand_` or equals `brands_t` → **"Brand"**
   - starts with `comp` → **"Competitors"**

4. **EU1 detection** — If any part equals `eu1` → **"EU"**

5. **Extract cluster_val** from position [5] (PRM) or [2] (short format)

6. **CRM safety net** — If `crm` in cluster_val → skip

7. **AI/Max special case** — If cluster_val is `ai` or `max`:
   - Check geo map for region prefix → use geo cluster
   - Otherwise → **"Other"**

8. **Geo-based clusters** — Check region (position [0]) against GEO_CLUSTERS:

   | Region Prefix | Cluster |
   |---|---|
   | `br`, `br_pt` | Brazil |
   | `ca` | Canada |
   | `dach`, `de`, `german_de` | DACH |
   | `fr`, `fr_fr` | France |
   | `latam` | LATAM |
   | `mx` | Mexico |

9. **WW region** — If region is `ww` → **"WW"**

10. **Keyword matching** — Match cluster_val against `KEYWORD_CLUSTERS` list (ordered, first match wins):

    **Agent clusters** (all `startswith`):
    | Keyword | Cluster |
    |---|---|
    | `agent_aihr` | Agent - HR |
    | `agent_aifinance` | Agent - Finance |
    | `agent_aiit` | Agent - IT |
    | `agent_ailegal` | Agent - Legal |
    | `agent_ainote` | Agent - Note Taker |
    | `agent_aireal` | Agent - Real Estate |
    | `agent_aiwork_builder` | Agent - Work Agent |
    | `agent_aiwork_agent` | Agent - Work Agent |
    | `agent_aiwork_process` | Agent - Work Process |
    | `agent_aiconstruction` | Agent - Construction |
    | `agent_aimarketing` | Agent - Marketing |
    | `agent_aigeneric` | Agent - Generic |
    | `agent_aicomp` | Agent - Comp |

    **Product clusters** (mixed modes):
    | Keywords | Cluster | Mode |
    |---|---|---|
    | `project`, `pm_` | Project | startswith |
    | `projectgen` | Project | exact |
    | `task` | Task | startswith |
    | `gantt` | Gantt | startswith |
    | `timeline` | Gantt | exact |
    | `marketing`, `social_media` | Marketing | startswith |
    | `content_calendar`, `email_marketing` | Marketing | exact |
    | `schedule`, `shared`, `calendar` | Calendar | startswith |
    | `to_do` | To Do | startswith |
    | `checklist` | To Do | exact |
    | `construction`, `production`, `order_mg` | Logistics | startswith |
    | `logistics` | Logistics | exact |
    | `general`, `workflow`, `dashboards` | General | startswith/exact |
    | `kanban` | Competitors | exact |
    | `tech` | Tech | exact |
    | `planner`, `team`, `tracker`, `templates`, `all_categories` | Other | exact |

11. **Fallback** — `comp_` prefix → "Competitors"; `agent_ai` prefix → "Other"; everything else → **"Other"**

### 5.3 Computed Aggregate Clusters

- **"All"** — Sum of ALL clusters for a given week
- **"All Generic"** — Sum of all clusters EXCLUDING: Brand, Competitors, Agent - Work Agent, and ALL `Agent - *` clusters

---

## 6. Dashboard Tabs & Sections

### 6.1 Tab 1: 📊 WoW Report (Cluster-Level)

**Filter Bar:**
- **Cluster dropdown** — populated from `DATA` keys (includes "All", "All Generic", and all individual clusters)
- **Week dropdown** — populated from weeks in selected cluster's data

**KPI Cards (12, dynamic):**
Spend, Impressions, Clicks, CTR, Hard Signups, CPS, %Work Signups, Payers, CAC, VBB ROAS, Agents Created, CPAC — each showing value for selected week with WoW delta arrow

**Chart:**
- Chart.js line/bar chart
- Toggleable metric buttons: Spend, Signups, CPS, Payers, CAC, VBB ROAS, Agents Created, CPAC, %Work
- Shows selected metric over all weeks for current cluster

**Weekly Trend Table:**
- Columns: Week | Spend | Δ | Imp | Δ | Clicks | CTR | Signups | Δ | CPS | Work SU | %Work | Payers | Δ | CAC | VBB $ | VBB ROAS | AC | Δ | CPAC
- Sortable columns (click header to sort)

**Cluster Comparison Section:**
- Title: "🔍 Cluster Comparison"
- Multi-select cluster pills (toggle each on/off)
- Quick-select buttons: Select all, Clear, Geo Clusters, Agent Clusters
- Date range filter (From/To)
- Comparison table: Cluster | Spend | Imp | Clicks | CTR | Signups | CPS | %Work | Payers | CAC | VBB ROAS | AC | CPAC
- Sortable by any column

### 6.2 Tab 2: 🌍 Geo Report (Country-Level)

**Filter Bar:**
- **Brand/Non-Brand toggle** — two buttons switching between `GEO_DATA_BRAND` and `GEO_DATA_NONBRAND`
- **Geo search dropdown** — autocomplete text input with dropdown list of countries
- **Date range** — From/To date inputs

**KPI Cards:** Same 12 metrics as WoW tab, for selected geo

**Chart:** Same toggleable chart, for geo data

**Weekly Trend Table:** Same columns as WoW tab

**Geo Comparison Section:**
- Title: "🔍 Geo Comparison"
- Multi-select geo pills, quick-select buttons, date range filter
- Comparison table with same columns, sortable

---

## 7. JS Data Structures

### DATA (cluster-level)

```js
const DATA = {
  "Cluster Name": [
    {
      "spend": <float>,           // USD (already divided by 1M)
      "imp": <int>,               // impressions
      "clicks": <int>,
      "signups": <float>,         // Hard Signup (MCC)
      "work_signups": <float>,    // Hard signup Work goal (MCC)
      "payers": <int>,            // Paying (MCC), rounded to int
      "vbb_value": <float>,       // VBB dollar value
      "agents_created": <float>,  // Agent Created (MCC)
      "week": "YYYY-MM-DD"        // Wednesday date
    },
    // ... one entry per week
  ],
  // ... one entry per cluster
};
```

### GEO_DATA_BRAND / GEO_DATA_NONBRAND

```js
const GEO_DATA_BRAND = {
  "Country Name": [
    // same schema as DATA entries
    { "spend": ..., "imp": ..., "clicks": ..., "signups": ..., "work_signups": ..., "payers": ..., "vbb_value": ..., "agents_created": ..., "week": "YYYY-MM-DD" },
    // ...
  ],
  "All Geos": [ ... ],  // computed aggregate
  // ...
};
const GEO_DATA_NONBRAND = { /* identical structure */ };
```

---

## 8. Geo Report — Data Pipeline

Geo data is pulled **separately** from cluster data using a different GAQL resource:

```sql
SELECT geographic_view.country_criterion_id, segments.date,
       campaign.name, metrics.cost_micros, metrics.impressions, metrics.clicks
FROM geographic_view
WHERE segments.date BETWEEN '{START_DATE}' AND '{end_date}'
  AND campaign.advertising_channel_type = 'SEARCH'
```

- Country criterion IDs are resolved to country names via `geo_criterion_to_country()` — a hardcoded map of ~190 Google Ads criterion IDs
- **Brand vs non-brand split:** Each campaign is classified via `extract_cluster()`:
  - Brand account (`6073520942`) OR brand/comp keywords in name → **brand**
  - Everything else → **nonbrand**
- **"All Geos"** is a computed aggregate (sum of all individual geos per week)
- Conversion metrics use the same GAQL queries but with `geographic_view` resource

---

## 9. Refresh Process

### When

- **Trigger:** Manual run of `refresh.py` (by Nymeria agent)
- **Schedule:** Typically weekly or on-demand
- **Duration:** ~3-5 minutes (API calls to 5 accounts × 2 query types + geo queries)

### How

1. `refresh.py` queries all 5 Google Ads accounts via Funnel Gate
2. Campaign names are parsed through `extract_cluster()` to determine cluster
3. Daily data is aggregated into Wednesday-Tuesday weekly buckets
4. `DATA` JS constant in `index.html` is replaced via regex
5. Geo data is pulled separately with geographic_view
6. `GEO_DATA_BRAND` and `GEO_DATA_NONBRAND` constants are updated
7. Changes are committed and pushed to `main` branch
8. GitHub Pages auto-deploys within ~30 seconds

### Command Flags

| Command | What It Does |
|---|---|
| `python3 refresh.py` | Refreshes BOTH cluster data + geo data |
| `python3 refresh.py --geo-only` | Refreshes ONLY geo data (faster) |

### What refresh.py Updates

- ✅ `DATA` constant (cluster-level weekly data)
- ✅ `GEO_DATA_BRAND` / `GEO_DATA_NONBRAND` constants
- ✅ Git commit + push

### What refresh.py Does NOT Update

- ❌ Dashboard layout/HTML structure
- ❌ Chart.js configuration
- ❌ KPI card definitions (those render dynamically from DATA)

---

## 10. File Structure

```
monday-wow-dashboard/
├── index.html                  # Entire dashboard (HTML + CSS + JS + data)
├── refresh.py                  # Data refresh script (pulls from Google Ads)
├── dashboard-spec.md           # This file
├── geo-wow-report.html         # Legacy standalone geo report
├── wm-analysis.html            # WM analysis page
└── vbb-payers-correlation.html # VBB/payers correlation analysis
```

---

## 11. Visual Design

- **Theme:** Dark mode (#0f0f0f background)
- **Color palette:** Green (#1D9E75) positive, Red (#D85A30) negative, Amber (#eda100) warnings
- **Typography:** System fonts (-apple-system stack), 13px base
- **Layout:** Max-width 1400px, centered
- **Charts:** Chart.js 4.4.7 with custom dark theme configuration
- **Tables:** Alternating row backgrounds, right-aligned numerics, sortable headers (click to sort)
- **Tabs:** Two dashboard tabs (WoW Report / Geo Report) with toggle visibility

---

## 12. Adding New Components

### Adding a New Cluster

1. Add keyword → cluster mapping to `KEYWORD_CLUSTERS` in `refresh.py`
2. Run `refresh.py` to regenerate `DATA`
3. Dashboard auto-discovers clusters from `DATA` keys — **no HTML changes needed**
4. Update this spec with the new cluster

### Adding a New Metric

1. **If from Google Ads:** Add GAQL field to queries in `refresh.py`, include in weekly data structure
2. **If derived:** Add formula to frontend JS render functions
3. Update KPI cards, chart toggles, and table columns in `index.html`
4. Update this spec

### Adding a New Geo

Geos are **auto-discovered** from Google Ads `geographic_view` data. To support a new geo criterion ID, add the mapping to `geo_criterion_to_country()` in `refresh.py`.

---

## 13. Changelog

| Date | Change |
|---|---|
| 2026-08-26 | Added this spec file and download button |

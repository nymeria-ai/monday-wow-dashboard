# monday.com WoW & Geo Dashboard — Specification

> **Purpose:** Paste this into any AI agent to instantly rebuild, debug, or extend the dashboard. It is the single source of truth for how every number is pulled, calculated, and displayed.

---

## 1. What the Dashboard Is

A weekly HTML performance dashboard tracking monday.com's Google Ads Search campaigns across two views:
- **WoW Report** — week-over-week trend by campaign cluster, with comparison tool
- **Geo Report** — country-level breakdown, split by brand vs. non-brand

**Live URL:** https://nymeria-ai.github.io/monday-wow-dashboard/  
**Repo:** `nymeria-ai/monday-wow-dashboard` (GitHub Pages, `main` branch)  
**Build output:** `index.html` (single self-contained file, all JS/CSS inline, data embedded as JS constants)

---

## 2. Data Sources

### 2.1 Google Ads Accounts

| Account Name | Customer ID | Contents |
|---|---|---|
| Main | `3746504118` | Core campaigns (Project, Task, Calendar, etc.) |
| Verticals | `6629846296` | Vertical-specific campaigns (Marketing, Logistics, etc.) |
| Verticals2 | `9194503735` | Additional vertical campaigns |
| Locals | `9441310809` | Local/geo-specific campaigns |
| Brand | `6073520942` | Brand campaigns (all campaigns here = Brand cluster) |

Data is pulled via **Funnel Gate** (`http://localhost:9400/execute`) using GAQL queries. Requester: `nymeria`.

### 2.2 Date Window

- **Start:** `2026-06-01` (hardcoded `START_DATE` in `refresh.py`)
- **End:** Yesterday (dynamic — computed at refresh time as `today - 1 day`)

### 2.3 Week Definition

Weeks run **Wednesday → Tuesday** (not Monday → Sunday).

```python
def week_start_wed(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    wednesday = d - timedelta(days=(d.weekday() - 2) % 7)
    return wednesday.strftime("%Y-%m-%d")
```

**Why Wednesday?** VBB (offline conversion) data has a ~5-day lag. Wed–Tue weeks ensure any Tuesday's VBB data is complete before the week closes.

---

## 3. Campaign → Cluster Mapping

### 3.1 Campaign Naming Convention

Google Ads campaigns follow the pattern:
```
{region}-{lang}-prm-{product}-{category}-{cluster_val}-...
```
Or short format:
```
{region}-s-{cluster_val}-{match}-{device}-{variant}
```

The `cluster_val` is extracted from **position 5** (long format: `parts[5]`) or **position 2** (short format: `parts[2]`).

### 3.2 Exclusions (Never Shown)

Campaigns are completely excluded if their name contains any of:
`crm`, `service`, `globster`, `elevate`, `taka`

Also excluded: `lead_management`, `account_management`, `lead_agent` cluster values.

### 3.3 Special Rules (Applied Before Keyword Matching)

1. **Brand account** (`6073520942`) → always `Brand`, regardless of name
2. Campaign name contains `brand` or `brand_*` → `Brand`
3. Campaign name contains `comp*` → `Competitors`
4. Region = `eu1` → `EU`
5. Region in geo list (br, ca, dach, de, fr, latam, mx…) → geo cluster (see §3.5)
6. Region = `ww` → `WW`

### 3.4 Keyword → Cluster (OR logic, first match wins)

Applied to `cluster_val` extracted from campaign name:

| Keyword | Cluster | Match Mode |
|---|---|---|
| `agent_aihr` | Agent - HR | startswith |
| `agent_aifinance` | Agent - Finance | startswith |
| `agent_aiit` | Agent - IT | startswith |
| `agent_ailegal` | Agent - Legal | startswith |
| `agent_ainote` | Agent - Note Taker | startswith |
| `agent_aireal` | Agent - Real Estate | startswith |
| `agent_aiwork_builder` | Agent - Work Agent | startswith |
| `agent_aiwork_agent` | Agent - Work Agent | startswith |
| `agent_aiwork_process` | Agent - Work Process | startswith |
| `agent_aiconstruction` | Agent - Construction | startswith |
| `agent_aimarketing` | Agent - Marketing | startswith |
| `agent_aigeneric` | Agent - Generic | startswith |
| `agent_aicomp` | Agent - Comp | startswith |
| `project`, `projectgen`, `pm_` | Project | startswith/exact |
| `task` | Task | startswith |
| `gantt`, `timeline` | Gantt | startswith/exact |
| `marketing`, `social_media`, `content_calendar`, `email_marketing` | Marketing | various |
| `schedule`, `shared`, `calendar` | Calendar | startswith |
| `to_do`, `checklist` | To Do | startswith/exact |
| `construction`, `production`, `order_mg`, `logistics` | Logistics | startswith/exact |
| `general`, `workflow`, `dashboards` | General | startswith/exact |
| `kanban` | Competitors | exact |
| `tech` | Tech | exact |
| `planner`, `team`, `tracker`, `templates`, `all_categories` | Other | exact |

### 3.5 Geo Clusters (Region Prefix → Cluster)

| Region Prefix | Cluster |
|---|---|
| `br`, `br_pt` | Brazil |
| `ca` | Canada |
| `dach`, `de`, `german_de` | DACH |
| `fr`, `fr_fr` | France |
| `latam`, `mx` | LATAM |

### 3.6 Computed Aggregate Clusters

After raw data is collected, two aggregates are computed:

- **All** — sum of ALL clusters (every cluster except the aggregates themselves)
- **All Generic** — sum of clusters that are NOT: `All`, `All exc. Brand`, `All Generic`, `Brand`, `Competitors`, `Agent - Work Agent` and do NOT start with `"Agent"`

---

## 4. Metrics — How Each Is Pulled

### 4.1 GAQL Queries (per account, per date range)

**Query 1 — Performance** (from `campaign` resource):
```sql
SELECT campaign.name, segments.date,
       metrics.cost_micros, metrics.impressions, metrics.clicks
FROM campaign
WHERE segments.date BETWEEN '{START_DATE}' AND '{end_date}'
AND campaign.advertising_channel_type = 'SEARCH'
```

**Query 2 — Conversions** (from `campaign` resource):
```sql
SELECT campaign.name, segments.date,
       segments.conversion_action_name,
       metrics.all_conversions, metrics.all_conversions_value
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

**⚠️ LOCKED METRIC DEFINITIONS (do not change without Tal's approval):**
- Hard Signups = `"Hard Signup (MCC)"` — ctID 402542787
- Payers = `"Paying (MCC)"` — ctID 241978033
- VBB ROAS = value of `"VBB - HT prod - offline conversions"` — ctID 7277286158
- All three use `metrics.all_conversions` (secondary actions, not primary)

### 4.2 Conversion Action → Data Field Mapping

| Conversion Action Name | Field in Data |
|---|---|
| `Hard Signup (MCC)` | `signups` |
| `Hard signup Work goal (MCC)` | `work_signups` |
| `Paying (MCC)` | `payers` |
| `Agent Created (MCC)` | `agents_created` |
| `VBB - HT prod - offline conversions` | `vbb_value` (uses `all_conversions_value`, not count) |

### 4.3 Derived Metrics (computed in JS at render time)

| Metric | Formula | Display |
|---|---|---|
| CTR | clicks / impressions | % |
| CR (Signup Rate) | signups / clicks | % |
| %Work | work_signups / signups | % |
| VBB ROAS | vbb_value / spend | × (e.g. 2.4x) |
| CPS (Cost per Signup) | spend / signups | $ |
| CVR Paying | payers / signups | % |

**Note on spend units:** GAQL returns `cost_micros` (integer). Divide by 1,000,000 for dollars. Stored as dollars in the data.

---

## 5. Dashboard Sections

### 5.1 Tab Navigation
Two tabs at the top:
- **📊 WoW Report** — cluster-level view
- **🌍 Geo Report** — country-level view

---

### 5.2 WoW Report Tab

#### Controls
- **Cluster selector** — dropdown of all available clusters (populated from `DATA` keys)
- **Date range** — From / To date pickers (filters which weeks are shown in chart and table)
- **Chart metric toggles** — buttons to switch the chart between: Spend, Impressions, Signups, %Work, Payers, VBB ROAS

#### KPI Cards
Four cards showing totals for the selected date range and cluster:
`Spend | Impressions | Signups | Payers`

Each card shows the period total and a WoW delta (last week vs. prior week).

#### Line Chart
Weekly trend of the selected metric for the selected cluster. Canvas-based (Chart.js style, inline).

#### Weekly Trend Table
Columns: `Week | Spend | Δ | Impressions | Δ | Signups | Δ | CPS | %Work | Δ | Payers | Δ | VBB ROAS | Δ`

One row per week within the selected date range. Δ = week-over-week change (color-coded green/red).

#### Cluster Comparison Tool (below section divider)
Compare multiple clusters side-by-side for a chosen timeframe.

- **Date range** — separate From/To for the comparison
- **Cluster pills** — click to select/deselect; quick-select buttons: `Select All`, `Clear`, `Agent Clusters`, `Geo Clusters`
- **Comparison table** columns: `Cluster | Spend | Impressions | CTR | Signups | CR | %Work | Payers | CVR Pay | VBB ROAS`
- Sortable by any column (click header)

---

### 5.3 Geo Report Tab

#### Controls
- **Brand toggle** — `Non-Brand` (default) / `Brand` buttons
- **Geo search/select** — searchable dropdown to pick a country (or "All Geos")
- **Date range** — From / To date pickers

#### KPI Cards
Same four metrics as WoW tab, but for the selected geo.

#### Line Chart
Weekly trend for the selected geo.

#### Weekly Trend Table
Same columns as WoW tab but for the selected geo.

#### Geo Comparison Tool (below section divider)
Compare multiple countries side-by-side.

- **Date range** — separate From/To
- **Region quick-select** — `Select All`, `Clear`, `NAM`, `EMEA`, `APJ`, `LATAM`
- **Comparison table** columns: `Country | Spend | Impressions | CTR | Signups | CR | %Work | Payers | CVR Pay | VBB ROAS`
- Sortable by any column

---

## 6. Data Structure in HTML

Data is embedded as JavaScript constants in `index.html`:

```js
const DATA = { "ClusterName": [ {week, spend, imp, clicks, signups, work_signups, payers, vbb_value, agents_created}, ... ], ... };
const GEO_DATA_BRAND = { "CountryName": [ {week, ...same fields...}, ... ], ... };
const GEO_DATA_NONBRAND = { "CountryName": [ {week, ...same fields...}, ... ], ... };
```

Each array is sorted by `week` (ascending, Wed-start ISO dates). All clusters have the same weeks array (zeros for weeks with no activity).

---

## 7. Refresh Pipeline

### 7.1 Script

Single script: `/tmp/monday-wow-dashboard/refresh.py` (also at `public/wow-dashboard/` in ff-sem-deploy)

Running `python3 refresh.py` does ALL of the following in sequence:
1. Pull cluster-level spend + conversions from all 5 accounts → updates `DATA`
2. Pull geo-level spend + conversions from all 5 accounts → updates `GEO_DATA_BRAND` + `GEO_DATA_NONBRAND`
3. Writes updated constants into `index.html` via regex replace
4. `git add index.html`, `git commit`, `git push`

Run with `--geo-only` to refresh only the geo data.

### 7.2 Refresh Cadence

**Manual — no automated cron.** The team runs it on demand when fresh data is needed.

### 7.3 Authentication

- **Funnel Gate:** running locally at `http://localhost:9400/execute`
- **Google Ads token:** stored in Funnel Gate vault (`nymeria` agent, `google_ads` platform)
- **GitHub push:** PAT via git remote (already configured in the cloned repo)

### 7.4 How Refresh Updates the HTML

The script uses `re.subn()` to replace:
```
const DATA = {...};
const GEO_DATA_BRAND = {...};
const GEO_DATA_NONBRAND = {...};
```
…with freshly-computed JSON. The rest of the HTML (UI, JS logic, CSS) is untouched.

---

## 8. How to Add a New Cluster

1. Add a `(keyword, "New Cluster Name", mode)` entry to `KEYWORD_CLUSTERS` in `refresh.py` (insert before more-generic entries to prevent false matches)
2. Re-run `refresh.py` to regenerate data
3. The UI auto-populates clusters from `Object.keys(DATA)` — no HTML changes needed
4. Update this document: add a row to §3.4

---

## 9. How to Add a New Metric

1. Add the conversion action name to `CONV_ACTIONS` in `refresh.py` and map it to a new key (e.g. `"New Action (MCC)": "new_field"`)
2. Initialize the new field to `0` in `cluster_data` and `data` defaultdicts
3. Add the aggregation logic in the perf/conv processing loops
4. Add the field to the `rows.append({...})` call in `format_data_for_html()` and `format_geo_data_for_html()`
5. Add the derived formula to §4.3 of this document
6. Add the column to the HTML table headers and the JS rendering logic

---

## 10. Changelog

| Date | Change | Who |
|---|---|---|
| 2026-08-26 | This spec document created and embedded in dashboard | Tal Herman / Ygritte |
| 2026-08-26 | Download spec button added (top-right of header) | Ygritte |
| ~2026-08-19 | Last data refresh (week ending Aug 19) | Nymeria |
| 2026-07-30 | Metric definitions locked (Hard Signups, Payers, VBB ROAS) | Tal Herman |
| 2026-07-30 | VBB ROAS verified using `all_conversions` (secondary actions) | Tal Herman |
| 2026-07-08 | Agent clusters added (HR, Finance, IT, Legal, etc.) | SEM team |
| 2026-06-01 | Dashboard launched (START_DATE) | SEM team |

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

The `cluster_val` is extracted from **position 5** (long format) or **position 2** (short format).

### 3.2 Exclusions (Never Shown)

Campaigns are completely excluded if their name contains any of:
`crm`, `service`, `globster`, `elevate`, `taka`, `lead_management`, `account_management`, `lead_agent`

### 3.3 Special Rules (Applied Before Keyword Matching)

1. **Brand account** (`6073520942`) → always `Brand`, regardless of name
2. Campaign name contains `brand` or `brand_*` → `Brand`
3. Campaign name contains `comp*` → `Competitors`
4. Region = `eu1` → `EU`
5. Region in geo list → geo cluster (see §3.5)
6. Region = `ww` → `WW`

### 3.4 Keyword → Cluster (OR logic, first match wins)

| Keyword | Cluster | Match Mode | Notes |
|---|---|---|---|
| `agent_aihr` | Agent - HR | startswith | |
| `agent_aifinance` | Agent - Finance | startswith | |
| `agent_aiit` | Agent - IT | startswith | |
| `agent_ailegal` | Agent - Legal | startswith | |
| `agent_ainote` | Agent - Note Taker | startswith | |
| `agent_aireal` | Agent - Real Estate | startswith | |
| `agent_aiwork_builder` | Agent - Work Agent | startswith | |
| `agent_aiwork_agent` | Agent - Work Agent | startswith | |
| `agent_aipmo_work_process` | Agent - Work Process | startswith | **New name** — same cluster as `agent_aiwork_process` |
| `agent_aiwork_process` | Agent - Work Process | startswith | **Legacy name** — kept for historical data |
| `agent_aipmo` | Agent - AI PMO | startswith | Must come AFTER `agent_aipmo_work_process` to avoid false match |
| `agent_aiconstruction` | Agent - Construction | startswith | |
| `agent_aimarketing` | Agent - Marketing | startswith | |
| `agent_aigeneric` | Agent - Generic | startswith | |
| `agent_aicomp` | Agent - Comp | startswith | |
| `project`, `projectgen`, `pm_` | Project | startswith/exact | |
| `task` | Task | startswith | |
| `gantt`, `timeline` | Gantt | startswith/exact | |
| `marketing`, `social_media`, `content_calendar`, `email_marketing` | Marketing | various | |
| `schedule`, `shared`, `calendar` | Calendar | startswith | |
| `to_do`, `checklist` | To Do | startswith/exact | |
| `construction`, `production`, `order_mg`, `logistics` | Logistics | startswith/exact | |
| `general`, `workflow`, `dashboards` | General | startswith/exact | |
| `kanban` | Competitors | exact | |
| `tech` | Tech | exact | |
| `planner`, `team`, `tracker`, `templates`, `all_categories` | Other | exact | |

> ⚠️ **Order matters** — `agent_aipmo_work_process` must appear before `agent_aipmo` in the list because `startswith` matching would otherwise catch `agent_aipmo_work_process` under the `agent_aipmo` rule.

### 3.5 Geo Clusters (Region Prefix → Cluster)

| Region Prefix | Cluster |
|---|---|
| `br`, `br_pt` | Brazil |
| `ca` | Canada |
| `dach`, `de`, `german_de` | DACH |
| `fr`, `fr_fr` | France |
| `latam`, `mx` | LATAM |

### 3.6 Computed Aggregate Clusters

- **All** — sum of ALL clusters
- **All Generic** — sum of clusters that are NOT: `All`, `All exc. Brand`, `All Generic`, `Brand`, `Competitors`, `Agent - Work Agent`, or any cluster starting with `"Agent"`

---

## 4. Metrics

### 4.1 GAQL Queries

**Query 1 — Performance:**
```sql
SELECT campaign.name, segments.date,
       metrics.cost_micros, metrics.impressions, metrics.clicks
FROM campaign
WHERE segments.date BETWEEN '{START_DATE}' AND '{end_date}'
AND campaign.advertising_channel_type = 'SEARCH'
```

**Query 2 — Conversions:**
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

### 4.2 ⚠️ Locked Metric Definitions (Do Not Change Without Tal's Approval)

| Metric | Conversion Action | ctID | Field | Value Source |
|---|---|---|---|---|
| Hard Signups | `Hard Signup (MCC)` | 402542787 | `signups` | `all_conversions` |
| Work Signups | `Hard signup Work goal (MCC)` | 318041244 | `work_signups` | `all_conversions` |
| Payers | `Paying (MCC)` | 241978033 | `payers` | `all_conversions` |
| Agents Created | `Agent Created (MCC)` | 7638407984 | `agents_created` | `all_conversions` |
| VBB | `VBB - HT prod - offline conversions` | 7277286158 | `vbb_value` | `all_conversions_value` (not count) |

All use `metrics.all_conversions` (secondary actions). VBB uses `all_conversions_value`. Verified by Tal Herman 2026-07-30.

### 4.3 Derived Metrics (computed in JS)

| Metric | Formula |
|---|---|
| CTR | clicks / impressions |
| CR (Signup Rate) | signups / clicks |
| %Work | work_signups / signups |
| VBB ROAS | vbb_value / spend |
| CPS | spend / signups |
| CVR Paying | payers / signups |

---

## 5. Dashboard Sections

### 5.1 Tab Navigation
- **📊 WoW Report** — cluster-level view
- **🌍 Geo Report** — country-level view

### 5.2 WoW Report Tab
- **Controls:** Cluster selector, date range (From/To), chart metric toggles
- **KPI Cards:** Spend | Impressions | Signups | Payers (period total + WoW delta)
- **Line Chart:** Weekly trend of selected metric
- **Weekly Trend Table:** Week | Spend | Δ | Impressions | Δ | Signups | Δ | CPS | %Work | Δ | Payers | Δ | VBB ROAS | Δ
- **Cluster Comparison Tool:** Multi-cluster side-by-side. Quick-select: All, Clear, Agent Clusters, Geo Clusters

### 5.3 Geo Report Tab
- **Controls:** Brand/Non-Brand toggle, geo search dropdown, date range
- **KPI Cards + Chart + Weekly Table:** Same structure as WoW tab
- **Geo Comparison Tool:** Multi-country side-by-side. Quick-select: NAM, EMEA, APJ, LATAM

---

## 6. Data Structure in HTML

```js
const DATA = { "ClusterName": [ {week, spend, imp, clicks, signups, work_signups, payers, vbb_value, agents_created}, ... ] };
const GEO_DATA_BRAND = { "CountryName": [ {week, ...same fields...}, ... ] };
const GEO_DATA_NONBRAND = { "CountryName": [ {week, ...same fields...}, ... ] };
```

Weeks are sorted ascending, Wed-start ISO dates. All clusters carry the same week array (zeros for inactive weeks).

---

## 7. Refresh Pipeline

Single script: `refresh.py` in repo root.

- `python3 refresh.py` — refresh both cluster + geo data, write to `index.html`, git commit + push
- `python3 refresh.py --geo-only` — refresh geo data only

**Auth:** Funnel Gate at `http://localhost:9400/execute`, requester `nymeria`. Google Ads token in vault.  
**Cadence:** Manual (no automated cron).

---

## 8. How to Add a New Cluster

1. Add `(keyword, "Cluster Name", mode)` to `KEYWORD_CLUSTERS` in `refresh.py` — insert before more-generic entries
2. Re-run `refresh.py`
3. UI auto-populates from `Object.keys(DATA)` — no HTML changes needed
4. Update §3.4 of this document

---

## 9. Changelog

| Date | Change |
|---|---|
| 2026-08-26 | Added `Agent - AI PMO` cluster (`agent_aipmo`). Added `agent_aipmo_work_process` as alias for `Agent - Work Process` (same cluster, new campaign name — Tal Herman) |
| 2026-08-26 | Download spec button added; this spec file created |
| ~2026-08-26 | Work Signups + Work CPS columns added (Nymeria) |
| ~2026-08-26 | Work SU removed from KPI cards and delta columns (Nymeria) |
| 2026-07-30 | Metric definitions locked: Hard Signups, Payers, VBB ROAS (Tal Herman) |
| 2026-06-01 | Dashboard launched (START_DATE) |

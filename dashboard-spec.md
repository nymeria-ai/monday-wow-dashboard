# SEM Performance Dashboard — Dashboard Specification

> **Purpose:** This document is the single source of truth for rebuilding, understanding, or extending the WoW & Geo SEM Performance Dashboard. Paste it to an AI agent and it can fully reconstruct the dashboard or answer any question about how it works.

> **⚠️ MANDATORY:** This spec MUST be updated every time the dashboard is modified — whether it's a new metric, cluster, tab, column, filter, goal ID, account, visual change, or any structural change to Google, Bing, or Geo views. No exception.

> **Last updated:** 2026-08-27

---

## 1. Overview

A static HTML dashboard (GitHub Pages) tracking week-over-week SEM performance for monday.com across Google Ads and Microsoft Ads (Bing) Search campaigns, organized by **cluster** (product/geo/agent groupings) and by **geo** (country-level breakdowns with brand/non-brand split, Google only).

- **URL:** `https://nymeria-ai.github.io/monday-wow-dashboard/`
- **Repo:** `nymeria-ai/monday-wow-dashboard` (GitHub, `main` branch)
- **Stack:** Single `index.html` with inline CSS + vanilla JS + Chart.js 4.4.7 (CDN). No frameworks, no build step.
- **Data:** Embedded as JS objects (`DATA`, `GEO_DATA_BRAND`, `GEO_DATA_NONBRAND`, `BING_DATA`) inside `<script>` tags. No external API calls at runtime.
- **Refresh scripts:** `refresh.py` (Google Ads), `bing_refresh.py` (Microsoft Ads / Bing)

---

## 2. Dashboard Tabs

The dashboard has **3 tabs**:

| Tab | Data Source | Description |
|---|---|---|
| 📊 WoW Report | Google Ads | Cluster-level WoW performance across 5 Google Ads accounts |
| 🌍 Geo Report | Google Ads | Country-level breakdowns with brand/non-brand split |
| 📊 Bing WoW | Microsoft Ads | Cluster-level WoW performance across 3 Bing accounts |

---

## 3. Data Sources & API Access

### 3.1 Google Ads Accounts

All data is pulled from **5 Google Ads accounts** under the monday.com MCC (`764-577-9471`):

| Account ID | Name | Notes |
|---|---|---|
| `3746504118` | Main | Core product campaigns |
| `6629846296` | Verticals | Vertical-specific campaigns |
| `9194503735` | Verticals2 | Overflow verticals |
| `9441310809` | Locals | Localized campaigns |
| `6073520942` | Brand | Brand campaigns (always → "Brand" cluster) |

**API Access:** All queries go through **Funnel Gate** (`http://localhost:9400/execute`). Never call Google Ads API directly.

### 3.2 Microsoft Ads (Bing) Accounts

Data pulled from **3 Microsoft Ads accounts** under Customer ID `21132515`:

| Account ID | Name | Notes |
|---|---|---|
| `50033985` | dapulse | Main account |
| `135096643` | Monday.com - Big 4 | Big 4 markets |
| `135096648` | Monday.com - Locals | Localized campaigns |

**API Access:** Uses `bingads` Python SDK with OAuth via `marketingaibuilders@monday.com`. Credentials in `~/.openclaw/workspace/.secrets/microsoft-ads-*.md`.

**Auth:** `OAuthWebAuthCodeGrant` with tenant-specific endpoint. Redirect URI: `https://login.microsoftonline.com/common/oauth2/nativeclient`.

### 3.3 Data Range

- **Start date:** `2026-06-01` (hardcoded in both scripts as `START_DATE`)
- **End date:** Yesterday (dynamically computed at refresh time)

---

## 4. Week Definition

### ⚠️ Wednesday to Tuesday (NOT Monday–Sunday!)

**Reason:** VBB (Value-Based Bidding) conversion data has a **5-day reporting lag**. By using Wednesday–Tuesday weeks, the VBB conversions from Wednesday (start of week) have had 5+ full days to report by the time the week closes on Tuesday.

**Function:** `week_start_wed(date_str)` — converts any date to the Wednesday that starts its Wed–Tue week.

---

## 5. Metrics

### 5.1 Google Ads Metrics

### ╔══════════════════════════════════════════════════════════════╗
### ║  LOCKED METRIC DEFINITIONS — DO NOT CHANGE WITHOUT TAL'S OK ║
### ╚══════════════════════════════════════════════════════════════╝

All conversion metrics use `metrics.all_conversions` (secondary actions). Verified by Tal Herman on 2026-07-30.

| Conversion Action Name | Field Used | Maps To | ctID |
|---|---|---|---|
| `Hard Signup (MCC)` | `metrics.all_conversions` | `signups` | 402542787 |
| `Hard signup Work goal (MCC)` | `metrics.all_conversions` | `work_signups` | 318041244 |
| `Paying (MCC)` | `metrics.all_conversions` | `payers` | 241978033 |
| `Agent Created (MCC)` | `metrics.all_conversions` | `agents_created` | 7638407984 |
| `VBB - HT prod - offline conversions` | `metrics.all_conversions_value` ⚠️ | `vbb_value` | 7277286158 |

**Critical:** VBB uses `all_conversions_value` (the dollar value), NOT `all_conversions` (the count).

### 5.2 Bing Metrics

Only **Hard Signups** and **Work Signups** — no VBB, Agents Created, or Payers on Bing.

| Goal Name | Goal IDs (accumulated) | Maps To |
|---|---|---|
| Hard Signups | `20117320` + `31018720` | `signups` |
| Work Signups | `31008558` + `31018719` | `work_signups` |

Data pulled via `GoalsAndFunnelsReportRequest` with `AllConversions` column. GoalId filtering done in code (not SOAP filter).

### 5.3 Derived / Calculated Metrics (Frontend)

| Metric | Formula | Notes |
|---|---|---|
| **CPS** / Hard Signup CPS | `spend / signups` | `—` if signups = 0 |
| **CAC** | `spend / payers` | Google only |
| **CPAC** | `spend / agents_created` | Google only |
| **VBB ROAS** | `vbb_value / spend` | Google only |
| **Work CPS** | `spend / work_signups` | |
| **WoW Δ** | `(this - prev) / prev` | `▲ X.X%` or `▼ X.X%` |

---

## 6. Campaign Clustering

The `extract_cluster()` function maps each campaign name to a dashboard cluster. Same logic in both `refresh.py` and `bing_refresh.py`.

### 6.1 Key Rules

1. **Comp/Brand ALWAYS wins over geo.** A campaign like `eu1-...-comp1-...` goes to "Competitors" only, NOT to "EU Generic".
2. **Geo clusters are labeled "Generic"** (e.g., "Canada Generic", "DACH Generic") to indicate they exclude comp activity.
3. **The Competitors cluster includes all geos** — it's the only place to see comp activity regardless of geography.
4. A note appears on both Google and Bing views explaining this.

### 6.2 Processing Order (first match wins)

1. **Exclusions** — Skip if campaign name contains: `crm`, `service`, `globster`, `elevate`, `taka`, `lead_management`, `account_management`, `lead_agent`
2. **Brand account** — Account `6073520942` (Google only) → always **"Brand"**
3. **Brand keywords** — `brand`, `brand_*`, `brands_t` → **"Brand"**
4. **Comp keywords** — starts with `comp` → **"Competitors"**
5. **EU1** — part equals `eu1` → **"EU Generic"**
6. **Geo mapping** — region prefix → **"[Geo] Generic"**:
   - `br`, `br_pt` → Brazil Generic
   - `ca` → Canada Generic
   - `dach`, `de`, `german_de` → DACH Generic
   - `fr`, `fr_fr` → France Generic
   - `latam` → LATAM Generic
   - `mx` → Mexico Generic
7. **WW** → "WW"
8. **Keyword matching** — cluster_val matched against `KEYWORD_CLUSTERS` list (Agent clusters, Product clusters, etc.)
9. **Fallback** → "Other"

### 6.3 Computed Aggregate Clusters

- **"All"** — Sum of ALL clusters
- **"All exc. Brand"** — Computed in frontend JS (all except Brand)
- **"All Generic"** — Sum excluding Brand, Competitors, Agent - Work Agent, and all `Agent - *` clusters

---

## 7. Tab-Specific Behavior

### 7.1 Google WoW Tab

- **Cluster dropdown:** All clusters visible (including Agent clusters)
- **KPI cards (12):** Spend, Impressions, Clicks, CTR, Hard Signups, CPS, %Work, Payers, CAC, VBB ROAS, Agents Created, CPAC
- **Chart:** Toggleable metrics, each gets its own y-axis. Bars for volume metrics, lines for cost metrics.
- **Table columns:** Week, Spend, Δ, Imp, Δ, Signups, CPS, Δ, Work SU, Work CPS, Δ, Agents Created, CPAC, Δ, Payers, CAC, VBB ROAS, Δ
- **Cluster Comparison:** Quick-select buttons: Select all, Clear, Generic, Agentic Activity, Geo Clusters

### 7.2 Bing WoW Tab

- **Agent clusters are HIDDEN** from the dropdown and comparison section (but their data is included in "All" totals)
- **No sub-header** — uses the general dashboard header only
- **KPI cards (4):** Spend, Hard Signups, Hard Signup CPS, Work CPS — showing **aggregated totals** for selected date range with delta vs equivalent prior period
- **Chart:** Bars for Spend/Impressions/Signups/Work SU, lines for CPS metrics. Each metric gets its own y-axis. Default active: Spend + Hard Signup CPS.
- **Table columns:** Week, Spend, Δ, Imp, Δ, Hard Signups, Hard SU CPS, Δ, Work SU, Work CPS, Δ (NO Agents Created, CPAC, Payers, CAC, VBB ROAS)
- **Cluster Comparison:** Quick-select buttons: Select all, Clear, Generic, Geo Clusters (no Agentic Activity button)

### 7.3 Geo Report Tab (Google only)

- Brand/Non-Brand toggle
- Geo search with autocomplete dropdown
- Same 12 KPI cards as Google WoW
- Country-level data from `geographic_view` resource

---

## 8. JS Data Structures

### DATA (Google cluster-level)
```js
const DATA = {
  "Cluster Name": [
    { "spend": <float>, "imp": <int>, "clicks": <int>, "signups": <float>,
      "work_signups": <float>, "payers": <int>, "vbb_value": <float>,
      "agents_created": <float>, "week": "YYYY-MM-DD" },
    // ... one per week
  ]
};
```

### BING_DATA (Bing cluster-level)
```js
const BING_DATA = {
  "Cluster Name": [
    { "spend": <float>, "imp": <int>, "clicks": <int>,
      "signups": <float>, "work_signups": <float>, "week": "YYYY-MM-DD" },
    // ... one per week (NO payers, vbb_value, agents_created)
  ]
};
```

### GEO_DATA_BRAND / GEO_DATA_NONBRAND (Google geo-level)
```js
const GEO_DATA_BRAND = {
  "Country Name": [ /* same schema as DATA entries */ ],
  "All Geos": [ ... ]  // computed aggregate
};
```

---

## 9. Refresh Process

### Google Refresh (`refresh.py`)

| Command | What It Does |
|---|---|
| `python3 refresh.py` | Refreshes BOTH cluster data + geo data |
| `python3 refresh.py --geo-only` | Refreshes ONLY geo data (faster) |

- Queries 5 Google Ads accounts via Funnel Gate
- Updates `DATA`, `GEO_DATA_BRAND`, `GEO_DATA_NONBRAND` in `index.html`
- Git commit + push

### Bing Refresh (`bing_refresh.py`)

| Command | What It Does |
|---|---|
| `python3 bing_refresh.py` | Refreshes Bing cluster data |

- Queries 3 Microsoft Ads accounts via bingads SDK (Reporting API)
- Report 1: `CampaignPerformanceReportRequest` (spend, impressions, clicks)
- Report 2: `GoalsAndFunnelsReportRequest` (conversions by GoalId)
- **GoalsAndFunnels scope:** Must use `AccountThroughAdGroupReportScope` (not Campaign scope)
- **Time object:** Must explicitly set `PredefinedTime = None` and `ReportTimeZone = None`
- **Required columns:** Must include `AccountName` and `Goal` (otherwise `RequiredColumnsNotSelected` error)
- **CSV parsing:** Header detection must match `"CampaignName"` with quotes (not just `Campaign`) to avoid matching report metadata lines
- Updates `BING_DATA` in `index.html`
- Git commit + push

### What Refresh Does NOT Update
- ❌ Dashboard layout/HTML structure
- ❌ Chart.js configuration
- ❌ KPI card definitions

---

## 10. File Structure

```
monday-wow-dashboard/
├── index.html                  # Entire dashboard (HTML + CSS + JS + data)
├── refresh.py                  # Google Ads data refresh script
├── bing_refresh.py             # Microsoft Ads (Bing) data refresh script
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
- **Charts:** Chart.js 4.4.7, each metric gets own y-axis, max-height 320px, bars for volume + lines for cost
- **Tables:** Alternating row backgrounds, right-aligned numerics
- **Tabs:** Three dashboard tabs with green active indicator
- **Cluster pills:** Purple on/off styling (#6366f1) in comparison sections

---

## 12. Adding New Components

### Adding a New Cluster
1. Add keyword → cluster mapping to `KEYWORD_CLUSTERS` in both `refresh.py` and `bing_refresh.py`
2. Run both refresh scripts
3. Dashboard auto-discovers clusters from data keys — no HTML changes needed
4. **Update this spec**

### Adding a New Metric
1. **Google:** Add GAQL field to queries in `refresh.py`, include in weekly data structure
2. **Bing:** Add GoalId to `HARD_SIGNUPS_GOAL_IDS` or `WORK_SIGNUPS_GOAL_IDS` in `bing_refresh.py`
3. Update frontend JS render functions, KPI cards, chart toggles, table columns
4. **Update this spec**

---

## 13. Changelog

| Date | Change |
|---|---|
| 2026-08-27 | **Bing WoW tab added.** 3 Microsoft Ads accounts, Hard Signups (Goal 20117320+31018720) + Work Signups (Goal 31008558+31018719). Agent clusters hidden from Bing view. |
| 2026-08-27 | **Geo clusters renamed to "Generic"** (e.g., "Canada Generic") to indicate comp exclusion. Comp/Brand always wins over geo. Note added to both views. |
| 2026-08-27 | **Bing chart:** Bars for volume metrics, lines for CPS. Each metric gets own y-axis. Default: Spend + Hard Signup CPS. |
| 2026-08-27 | **Bing KPIs:** Aggregate entire date range (not just last week). Delta vs equivalent prior period. |
| 2026-08-27 | Renamed Bing "Signups"→"Hard Signups", "CPS"→"Hard Signup CPS". Removed Bing sub-header. |
| 2026-08-26 | Added spec file and download button. Added Agent - PMO cluster. |

#!/usr/bin/env python3
"""
WoW Dashboard Refresh Script
Pulls data from all 5 Google Ads accounts via Funnel Gate,
maps campaigns to clusters, aggregates by ISO week,
and updates the DATA constant in index.html.

╔══════════════════════════════════════════════════════════════════╗
║  LOCKED METRIC DEFINITIONS — DO NOT CHANGE WITHOUT TAL'S OK    ║
║                                                                 ║
║  Hard Signups = "Hard Signup (MCC)" ctID 402542787              ║
║  Payers       = "Paying (MCC)"      ctID 241978033              ║
║  VBB ROAS     = value of "VBB - HT prod - offline conversions"  ║
║                                                                 ║
║  All three use metrics.all_conversions (secondary actions).      ║
║  Verified by Tal Herman on 2026-07-30.                          ║
╚══════════════════════════════════════════════════════════════════╝
"""
import json
import subprocess
import sys
import re
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
INDEX_HTML = SCRIPT_DIR / "index.html"
FUNNEL_GATE_URL = "http://localhost:9400/execute"

# All 5 accounts
ACCOUNTS = {
    "3746504118": "Main",
    "6629846296": "Verticals",
    "9194503735": "Verticals2",
    "9441310809": "Locals",
    "6073520942": "Brand",
}

# Start date for historical data (first week in dashboard)
START_DATE = "2026-06-01"  # Pull data from June 1st

# Conversion actions — LOCKED definitions (do NOT change without Tal's approval)
# Filtered by name in GAQL (ctID for reference only — can't filter by resource name across MCC).
# Hard Signups   = "Hard Signup (MCC)"                      ctID 402542787
# Work Signups   = "Hard signup Work goal (MCC)"            ctID 318041244
# Payers         = "Paying (MCC)"                           ctID 241978033
# Agents Created = "Agent Created (MCC)"                    ctID 7638407984
# VBB            = "VBB - HT prod - offline conversions"    ctID 7277286158
CONV_ACTIONS = {
    "Hard Signup (MCC)": "signups",
    "Hard signup Work goal (MCC)": "work_signups",
    "Paying (MCC)": "payers",
    "Agent Created (MCC)": "agents_created",
    "VBB - HT prod - offline conversions": "vbb",
}

# Campaign name exclusions — skip these niches/types entirely
CAMPAIGN_EXCLUSIONS = {"crm", "service", "globster", "elevate", "taka"}

# ── Geo-based cluster mapping (region prefix → cluster) ──
GEO_CLUSTERS = {
    "br": "Brazil",
    "br_pt": "Brazil",
    "ca": "Canada",
    "dach": "DACH",
    "de": "DACH",
    "german_de": "DACH",
    "fr": "France",
    "fr_fr": "France",
    "latam": "LATAM",
    "mx": "Mexico",
}

# ── Keyword → Cluster mapping (OR logic) ──
# Checked against the cluster_val extracted from position 5 (long) or 2 (short).
# Order matters: more specific keywords first to avoid false matches.
# Each entry: (keyword, cluster_name, match_mode)
#   match_mode: "startswith" = cluster_val.startswith(kw)
#               "exact" = cluster_val == kw
#               "contains" = kw in cluster_val
KEYWORD_CLUSTERS = [
    # Agent clusters (most specific — prefix match, before generic keywords)
    ("agent_aihr", "Agent - HR", "startswith"),
    ("agent_aifinance", "Agent - Finance", "startswith"),
    ("agent_aiit", "Agent - IT", "startswith"),
    ("agent_ailegal", "Agent - Legal", "startswith"),
    ("agent_ainote", "Agent - Note Taker", "startswith"),
    ("agent_aireal", "Agent - Real Estate", "startswith"),
    ("agent_aiwork_builder", "Agent - Work Agent", "startswith"),
    ("agent_aiwork_agent", "Agent - Work Agent", "startswith"),
    ("agent_aipmo_work_process", "Agent - Work Process", "startswith"),  # new name (same cluster as agent_aiwork_process)
    ("agent_aipmo_work_process", "Agent - Work Process", "startswith"),  # new name (same cluster as agent_aiwork_process)
    ("agent_aiwork_process", "Agent - Work Process", "startswith"),      # legacy name — keep for historical data
    ("agent_aipmo", "Agent - AI PMO", "startswith"),      # old name — keep for historical data
    ("agent_aipmo", "Agent - AI PMO", "startswith"),
    ("agent_aiconstruction", "Agent - Construction", "startswith"),
    ("agent_aimarketing", "Agent - Marketing", "startswith"),
    ("agent_aigeneric", "Agent - Generic", "startswith"),
    ("agent_aicomp", "Agent - Comp", "startswith"),
    # Specific clusters (before General to avoid "management" overlap)
    ("project", "Project", "startswith"),
    ("projectgen", "Project", "exact"),
    ("pm_", "Project", "startswith"),
    ("task", "Task", "startswith"),
    ("gantt", "Gantt", "startswith"),
    ("timeline", "Gantt", "exact"),
    ("marketing", "Marketing", "startswith"),
    ("social_media", "Marketing", "startswith"),
    ("content_calendar", "Marketing", "exact"),
    ("email_marketing", "Marketing", "exact"),
    ("schedule", "Calendar", "startswith"),
    ("shared", "Calendar", "startswith"),
    ("calendar", "Calendar", "startswith"),
    ("to_do", "To Do", "startswith"),
    ("checklist", "To Do", "exact"),
    ("construction", "Logistics", "startswith"),
    ("production", "Logistics", "startswith"),
    ("order_mg", "Logistics", "startswith"),
    ("logistics", "Logistics", "exact"),
    # General — no "management" to avoid matching project_management etc.
    ("general", "General", "startswith"),
    ("workflow", "General", "exact"),
    ("dashboards", "General", "exact"),
    # Other known keywords
    ("kanban", "Competitors", "exact"),
    ("tech", "Tech", "exact"),
    ("planner", "Other", "exact"),
    ("team", "Other", "exact"),
    ("tracker", "Other", "exact"),
    ("templates", "Other", "exact"),
    ("all_categories", "Other", "exact"),
]


def match_keyword_cluster(cluster_val: str) -> str | None:
    """Match a cluster_val against KEYWORD_CLUSTERS using OR logic."""
    for kw, cluster_name, mode in KEYWORD_CLUSTERS:
        if mode == "startswith" and cluster_val.startswith(kw):
            return cluster_name
        elif mode == "exact" and cluster_val == kw:
            return cluster_name
        elif mode == "contains" and kw in cluster_val:
            return cluster_name
    return None

def extract_cluster(campaign_name: str, account_id: str) -> str | None:
    """Extract dashboard cluster from campaign name. Returns None to skip."""
    # Clean: remove trailing suffixes after space (e.g. "VBB test1 - Calendar US H")
    base_name = campaign_name.split(" ")[0] if " " in campaign_name else campaign_name
    parts = base_name.split("-")
    parts_lower = [p.lower() for p in parts]
    name_lower = campaign_name.lower()

    # ── Exclusions — FIRST, before any cluster logic ──
    # Exclude if ANY part contains an excluded keyword
    if any(excl in p for p in parts_lower for excl in CAMPAIGN_EXCLUSIONS):
        return None
    # Also exclude known CRM-adjacent clusters
    if any(p in ("lead_management", "account_management", "lead_agent") for p in parts_lower):
        return None

    # ── Brand / Comp — BEFORE any other cluster logic ──
    # Brand account → always Brand
    if account_id == "6073520942":
        return "Brand"
    # If "brand" or "comp" appears anywhere in the campaign name → Brand / Competitors
    # Brand takes priority over Comp
    if any(p == "brand" or p.startswith("brand_") or p == "brands_t" for p in parts_lower):
        return "Brand"
    if any(p.startswith("comp") for p in parts_lower):
        return "Competitors"

    if len(parts) < 3:
        return "Other"

    # EU1 detection
    if any(p.lower() == "eu1" for p in parts):
        return "EU"

    region = parts[0].lower()

    # Detect format
    if len(parts) >= 6 and parts[2].lower() == "prm":
        # Long format: region-lang-prm-product-category-cluster-...
        cluster_val = parts[5].lower()
    elif len(parts) >= 3 and parts[1].lower() == "s":
        # Short format: region-s-cluster-match-desk-variant
        cluster_val = parts[2].lower()
    else:
        # Unknown format
        return "Other"

    # Double-check CRM in cluster value (safety net)
    if "crm" in cluster_val:
        return None

    # Special: AI Max campaigns
    if cluster_val in ("ai", "max"):
        # Geo-based if available, otherwise Other
        return GEO_CLUSTERS.get(region, "Other")

    # Check geo-based mapping first
    if region in GEO_CLUSTERS:
        return GEO_CLUSTERS[region]

    # WW region → WW cluster (before keyword matching)
    if region == "ww":
        return "WW"

    # Match cluster_val against keyword list (OR logic, priority order)
    matched = match_keyword_cluster(cluster_val)
    if matched:
        return matched

    # Fallback: try prefix matching for unknown comp_ / agent_ patterns
    if cluster_val.startswith("comp_"):
        return "Competitors"
    if cluster_val.startswith("agent_ai"):
        return "Other"  # Unknown agent type

    return "Other"


def week_start_wed(date_str: str) -> str:
    """Convert YYYY-MM-DD to the Wednesday that starts its Wed-Tue week.
    VBB data has a 5-day lag, so Wed-Tue weeks ensure complete VBB for
    any week whose Tuesday is ≥5 days ago."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    # weekday(): Mon=0, Tue=1, Wed=2, …, Sun=6
    # offset from Wednesday: (weekday - 2) % 7  →  Wed=0, Thu=1, …, Tue=6
    wednesday = d - timedelta(days=(d.weekday() - 2) % 7)
    return wednesday.strftime("%Y-%m-%d")


def run_gaql(customer_id: str, query: str) -> list:
    """Execute a GAQL query via Funnel Gate."""
    payload = {
        "requester": "nymeria",
        "action": "gaql_query",
        "platform": "google_ads",
        "scope": {
            "customer_id": customer_id,
            "query": query,
        },
        "trail": {"reasoning": "WoW dashboard weekly refresh"},
        "skill_name": "wow-dashboard-refresh",
        "initiator": {"name": "Nymeria", "context": "Cron - WoW dashboard refresh"},
    }
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", FUNNEL_GATE_URL,
         "-H", "Content-Type: application/json",
         "-d", json.dumps(payload)],
        capture_output=True, text=True, timeout=120
    )
    data = json.loads(result.stdout)
    if "error" in data:
        print(f"  ERROR for {customer_id}: {data['error']}", file=sys.stderr)
        return []
    return data.get("result", {}).get("results", [])


def pull_data():
    """Pull performance and conversion data from all accounts."""
    # Calculate date range
    today = datetime.now()
    end_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")  # Yesterday

    print(f"Pulling data from {START_DATE} to {end_date}")

    # Structure: {cluster: {week: {spend, imp, clicks, signups, work_signups, payers, vbb_value, agents_created}}}
    cluster_data = defaultdict(lambda: defaultdict(lambda: {
        "spend": 0, "imp": 0, "clicks": 0, "signups": 0, "work_signups": 0, "payers": 0, "vbb_value": 0, "agents_created": 0
    }))

    for acct_id, acct_name in ACCOUNTS.items():
        print(f"\n=== {acct_name} ({acct_id}) ===")

        # Query 1: Performance metrics
        perf_query = (
            f"SELECT campaign.name, segments.date, "
            f"metrics.cost_micros, metrics.impressions, metrics.clicks "
            f"FROM campaign "
            f"WHERE segments.date BETWEEN '{START_DATE}' AND '{end_date}' "
            f"AND campaign.advertising_channel_type = 'SEARCH'"
        )
        print(f"  Pulling performance metrics...")
        perf_rows = run_gaql(acct_id, perf_query)
        print(f"  Got {len(perf_rows)} performance rows")

        # Query 2: Conversions — all 4 actions by name (use all_conversions — secondary actions)
        conv_names = ", ".join(f"'{name}'" for name in CONV_ACTIONS.keys())
        conv_query = (
            f"SELECT campaign.name, segments.date, "
            f"segments.conversion_action_name, metrics.all_conversions, metrics.all_conversions_value "
            f"FROM campaign "
            f"WHERE segments.date BETWEEN '{START_DATE}' AND '{end_date}' "
            f"AND campaign.advertising_channel_type = 'SEARCH' "
            f"AND segments.conversion_action_name IN ({conv_names})"
        )
        print(f"  Pulling conversion metrics (4 actions by name)...")
        conv_rows = run_gaql(acct_id, conv_query)
        print(f"  Got {len(conv_rows)} conversion rows")

        # Process performance rows
        for row in perf_rows:
            camp_name = row.get("campaign", {}).get("name", "")
            date = row.get("segments", {}).get("date", "")
            metrics = row.get("metrics", {})

            cluster = extract_cluster(camp_name, acct_id)
            if cluster is None:
                continue  # Skip CRM

            week = week_start_wed(date)
            cost = float(metrics.get("costMicros", 0)) / 1_000_000
            imps = int(metrics.get("impressions", 0))
            clicks = int(metrics.get("clicks", 0))

            cluster_data[cluster][week]["spend"] += cost
            cluster_data[cluster][week]["imp"] += imps
            cluster_data[cluster][week]["clicks"] += clicks

        # Process conversion rows (all 4 actions by name)
        for row in conv_rows:
            camp_name = row.get("campaign", {}).get("name", "")
            date = row.get("segments", {}).get("date", "")
            conv_name = row.get("segments", {}).get("conversionActionName", "")
            metrics = row.get("metrics", {})

            cluster = extract_cluster(camp_name, acct_id)
            if cluster is None:
                continue

            week = week_start_wed(date)
            conversions = float(metrics.get("allConversions", 0))
            conv_value = float(metrics.get("allConversionsValue", 0))

            metric_key = CONV_ACTIONS.get(conv_name)
            if metric_key == "vbb":
                cluster_data[cluster][week]["vbb_value"] += conv_value
            elif metric_key:
                cluster_data[cluster][week][metric_key] += conversions

    return cluster_data


def compute_aggregates(cluster_data: dict) -> dict:
    """Compute 'All' and 'All exc. Brand' aggregates."""
    all_weeks = set()
    for weeks in cluster_data.values():
        all_weeks.update(weeks.keys())

    EXCLUDED_FROM_GENERIC = {"All", "All exc. Brand", "All Generic", "Brand", "Competitors", "Agent - Work Agent"}

    # All cluster + All Generic cluster
    for week in sorted(all_weeks):
        totals = {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "work_signups": 0, "payers": 0, "vbb_value": 0, "agents_created": 0}
        generic_totals = {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "work_signups": 0, "payers": 0, "vbb_value": 0, "agents_created": 0}
        for cluster_name, weeks in cluster_data.items():
            if cluster_name in ("All", "All exc. Brand", "All Generic"):
                continue
            if week in weeks:
                for k in totals:
                    totals[k] += weeks[week][k]
                # Generic = not agent, not brand, not comp
                if cluster_name not in EXCLUDED_FROM_GENERIC and not cluster_name.startswith("Agent"):
                    for k in generic_totals:
                        generic_totals[k] += weeks[week][k]
        cluster_data["All"][week] = totals
        cluster_data["All Generic"][week] = generic_totals

    return cluster_data


def format_data_for_html(cluster_data: dict) -> str:
    """Format cluster data as the JavaScript DATA constant."""
    # Get all weeks sorted
    all_weeks = set()
    for weeks in cluster_data.values():
        all_weeks.update(weeks.keys())
    sorted_weeks = sorted(all_weeks)

    # Build the data structure
    output = {}
    for cluster_name in sorted(cluster_data.keys()):
        if cluster_name == "All exc. Brand":
            continue
        weeks_data = cluster_data[cluster_name]
        rows = []
        for week in sorted_weeks:
            d = weeks_data.get(week, {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "work_signups": 0, "payers": 0, "vbb_value": 0, "agents_created": 0})
            rows.append({
                "spend": round(d["spend"], 2),
                "imp": d["imp"],
                "clicks": d["clicks"],
                "signups": round(d["signups"], 1),
                "work_signups": round(d["work_signups"], 1),
                "payers": int(round(d["payers"])),
                "vbb_value": round(d["vbb_value"], 2),
                "agents_created": round(d["agents_created"], 1),
                "week": week,
            })
        output[cluster_name] = rows

    return json.dumps(output, separators=(",", ":"))


def update_index_html(data_json: str):
    """Replace the DATA constant in index.html."""
    content = INDEX_HTML.read_text()

    # Match the DATA constant line
    pattern = r'const DATA = \{.*?\};'
    replacement = f'const DATA = {data_json};'

    new_content, count = re.subn(pattern, replacement, content, count=1, flags=re.DOTALL)
    if count == 0:
        print("ERROR: Could not find 'const DATA = {...};' in index.html", file=sys.stderr)
        sys.exit(1)

    INDEX_HTML.write_text(new_content)
    print(f"\nUpdated index.html ({len(data_json):,} chars of DATA)")


def git_commit_push():
    """Commit and push changes."""
    import subprocess as sp
    today = datetime.now().strftime("%Y-%m-%d")
    sp.run(["git", "add", "index.html"], cwd=SCRIPT_DIR, check=True)
    # Check if there are changes to commit
    result = sp.run(["git", "diff", "--cached", "--quiet"], cwd=SCRIPT_DIR)
    if result.returncode == 0:
        print("No changes to commit")
        return False
    sp.run(["git", "commit", "-m", f"Auto-refresh data {today}"], cwd=SCRIPT_DIR, check=True)
    sp.run(["git", "push"], cwd=SCRIPT_DIR, check=True)
    print(f"Committed and pushed: Auto-refresh data {today}")
    return True


def main():
    print("🔄 WoW Dashboard Refresh")
    print("=" * 50)

    cluster_data = pull_data()
    cluster_data = compute_aggregates(cluster_data)

    # Summary
    print(f"\n📊 Summary: {len(cluster_data)} clusters")
    for name in sorted(cluster_data.keys()):
        weeks = cluster_data[name]
        total_spend = sum(w["spend"] for w in weeks.values())
        print(f"  {name}: {len(weeks)} weeks, ${total_spend:,.0f} total spend")

    data_json = format_data_for_html(cluster_data)
    update_index_html(data_json)
    pushed = git_commit_push()

    if pushed:
        print("\n✅ Dashboard refreshed and deployed!")
    else:
        print("\n✅ No new data to deploy")





# ══════════════════════════════════════════════════════════════
# GEO REPORT — geographic_view based data (all countries)
# ══════════════════════════════════════════════════════════════

# Google Ads geographic criteria → country name mapping
# Comprehensive list covering all major geos
GEO_CRITERIA = {
    "2004":"Afghanistan","2008":"Albania","2012":"Algeria","2020":"Andorra","2024":"Angola",
    "2032":"Argentina","2036":"Australia","2040":"Austria","2048":"Bahrain","2050":"Bangladesh",
    "2056":"Belgium","2064":"Bhutan","2068":"Bolivia","2070":"Bosnia and Herzegovina",
    "2076":"Brazil","2096":"Brunei","2100":"Bulgaria","2104":"Myanmar","2116":"Cambodia",
    "2120":"Cameroon","2124":"Canada","2144":"Sri Lanka","2152":"Chile","2156":"China",
    "2158":"Taiwan","2170":"Colombia","2188":"Costa Rica","2191":"Croatia","2196":"Cyprus",
    "2203":"Czech Republic","2208":"Denmark","2214":"Dominican Republic","2218":"Ecuador",
    "2222":"El Salvador","2233":"Estonia","2242":"Fiji","2246":"Finland","2250":"France",
    "2268":"Georgia","2276":"Germany","2288":"Ghana","2300":"Greece","2320":"Guatemala",
    "2328":"Guyana","2332":"Haiti","2340":"Honduras","2344":"Hong Kong","2348":"Hungary",
    "2352":"Iceland","2356":"India","2360":"Indonesia","2364":"Iran","2368":"Iraq",
    "2372":"Ireland","2376":"Israel","2380":"Italy","2384":"Ivory Coast","2388":"Jamaica",
    "2392":"Japan","2398":"Kazakhstan","2400":"Jordan","2404":"Kenya","2408":"North Korea",
    "2410":"South Korea","2414":"Kuwait","2418":"Laos","2422":"Lebanon","2428":"Latvia",
    "2434":"Libya","2438":"Liechtenstein","2440":"Lithuania","2442":"Luxembourg",
    "2446":"Macau","2450":"Madagascar","2458":"Malaysia","2462":"Maldives","2466":"Mali",
    "2470":"Malta","2484":"Mexico","2496":"Mongolia","2498":"Moldova","2499":"Montenegro",
    "2504":"Morocco","2508":"Mozambique","2512":"Oman","2516":"Namibia","2524":"Nepal",
    "2528":"Netherlands","2554":"New Zealand","2558":"Nicaragua","2562":"Niger",
    "2566":"Nigeria","2578":"Norway","2586":"Pakistan","2591":"Panama","2598":"Papua New Guinea",
    "2600":"Paraguay","2604":"Peru","2608":"Philippines","2616":"Poland","2620":"Portugal",
    "2630":"Puerto Rico","2634":"Qatar","2642":"Romania","2643":"Russia","2682":"Saudi Arabia",
    "2686":"Senegal","2688":"Serbia","2702":"Singapore","2703":"Slovakia","2704":"Vietnam",
    "2705":"Slovenia","2710":"South Africa","2716":"Zimbabwe","2724":"Spain","2740":"Suriname",
    "2752":"Sweden","2756":"Switzerland","2764":"Thailand","2780":"Trinidad and Tobago",
    "2784":"United Arab Emirates","2788":"Tunisia","2792":"Turkey","2800":"Uganda",
    "2804":"Ukraine","2807":"North Macedonia","2818":"Egypt","2826":"United Kingdom",
    "2834":"Tanzania","2840":"United States","2854":"Burkina Faso","2858":"Uruguay",
    "2860":"Uzbekistan","2862":"Venezuela","2882":"Samoa",
    "2296":"Kiribati","2520":"Nauru","2548":"Vanuatu","2776":"Tonga","2798":"Tuvalu",
    "2583":"Micronesia","2584":"Marshall Islands","2585":"Palau",
    "2090":"Solomon Islands","2626":"Timor-Leste",
}


def is_brand_campaign(campaign_name: str, account_id: str) -> bool:
    """Check if a campaign is brand (vs non-brand)."""
    if account_id == "6073520942":
        return True
    base_name = campaign_name.split(" ")[0] if " " in campaign_name else campaign_name
    parts = [p.lower() for p in base_name.split("-")]
    return any(p == "brand" or p.startswith("brand_") for p in parts)


def should_exclude_geo(campaign_name: str) -> bool:
    """Check campaign name exclusions for geo report."""
    name_lower = campaign_name.lower()
    return any(excl in name_lower for excl in CAMPAIGN_EXCLUSIONS)


def pull_geo_data():
    """Pull geographic_view data from all 5 accounts, split by brand/nonbrand."""
    today = datetime.now()
    end_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"\n🌍 Pulling GEO data from {START_DATE} to {end_date}")

    empty_metrics = lambda: {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "work_signups": 0, "payers": 0, "vbb_value": 0, "agents_created": 0}
    data = {
        "brand": defaultdict(lambda: defaultdict(empty_metrics)),
        "nonbrand": defaultdict(lambda: defaultdict(empty_metrics)),
    }

    for acct_id, acct_name in ACCOUNTS.items():
        print(f"\n=== GEO: {acct_name} ({acct_id}) ===")

        # Query 1: Performance with geo
        perf_query = (
            f"SELECT campaign.name, campaign.advertising_channel_type, segments.date, geographic_view.country_criterion_id, "
            f"metrics.cost_micros, metrics.impressions, metrics.clicks "
            f"FROM geographic_view "
            f"WHERE segments.date BETWEEN '{START_DATE}' AND '{end_date}' "
            f"AND campaign.advertising_channel_type = 'SEARCH'"
        )
        print(f"  Pulling geo performance...")
        perf_rows = run_gaql(acct_id, perf_query)
        print(f"  Got {len(perf_rows)} geo perf rows")

        # Query 2: Conversions with geo
        conv_names = ", ".join(f"'{name}'" for name in CONV_ACTIONS.keys())
        conv_query = (
            f"SELECT campaign.name, campaign.advertising_channel_type, segments.date, geographic_view.country_criterion_id, "
            f"segments.conversion_action_name, metrics.all_conversions, metrics.all_conversions_value "
            f"FROM geographic_view "
            f"WHERE segments.date BETWEEN '{START_DATE}' AND '{end_date}' "
            f"AND campaign.advertising_channel_type = 'SEARCH' "
            f"AND segments.conversion_action_name IN ({conv_names})"
        )
        print(f"  Pulling geo conversions...")
        conv_rows = run_gaql(acct_id, conv_query)
        print(f"  Got {len(conv_rows)} geo conv rows")

        for row in perf_rows:
            camp_name = row.get("campaign", {}).get("name", "")
            date_str = row.get("segments", {}).get("date", "")
            geo_view = row.get("geographicView", {})
            criteria_id = str(geo_view.get("countryCriterionId", ""))
            metrics = row.get("metrics", {})

            if should_exclude_geo(camp_name):
                continue
            country = GEO_CRITERIA.get(criteria_id)
            if not country:
                continue

            brand_key = "brand" if is_brand_campaign(camp_name, acct_id) else "nonbrand"
            week = week_start_wed(date_str)

            data[brand_key][country][week]["spend"] += float(metrics.get("costMicros", 0)) / 1_000_000
            data[brand_key][country][week]["imp"] += int(metrics.get("impressions", 0))
            data[brand_key][country][week]["clicks"] += int(metrics.get("clicks", 0))

        for row in conv_rows:
            camp_name = row.get("campaign", {}).get("name", "")
            date_str = row.get("segments", {}).get("date", "")
            geo_view = row.get("geographicView", {})
            criteria_id = str(geo_view.get("countryCriterionId", ""))
            conv_name = row.get("segments", {}).get("conversionActionName", "")
            metrics = row.get("metrics", {})

            if should_exclude_geo(camp_name):
                continue
            country = GEO_CRITERIA.get(criteria_id)
            if not country:
                continue

            brand_key = "brand" if is_brand_campaign(camp_name, acct_id) else "nonbrand"
            week = week_start_wed(date_str)
            conversions = float(metrics.get("allConversions", 0))
            conv_value = float(metrics.get("allConversionsValue", 0))

            metric_key = CONV_ACTIONS.get(conv_name)
            if metric_key == "vbb":
                data[brand_key][country][week]["vbb_value"] += conv_value
            elif metric_key:
                data[brand_key][country][week][metric_key] += conversions

    return data


def compute_geo_aggregates(data: dict) -> dict:
    """Compute 'All Geos' aggregate for each brand key."""
    for brand_key in ("brand", "nonbrand"):
        all_weeks = set()
        for geo_weeks in data[brand_key].values():
            all_weeks.update(geo_weeks.keys())

        for week in sorted(all_weeks):
            totals = {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "work_signups": 0, "payers": 0, "vbb_value": 0, "agents_created": 0}
            for geo, weeks in data[brand_key].items():
                if geo == "All Geos":
                    continue
                if week in weeks:
                    for k in totals:
                        totals[k] += weeks[week][k]
            data[brand_key]["All Geos"][week] = totals

    return data


def format_geo_data_for_html(data: dict) -> tuple[str, str]:
    """Format geo data as two JS constants: GEO_DATA_BRAND, GEO_DATA_NONBRAND."""
    results = {}
    for brand_key in ("brand", "nonbrand"):
        all_weeks = set()
        for geo_weeks in data[brand_key].values():
            all_weeks.update(geo_weeks.keys())
        sorted_weeks = sorted(all_weeks)

        output = {}
        for geo in sorted(data[brand_key].keys()):
            weeks_data = data[brand_key][geo]
            rows = []
            for week in sorted_weeks:
                d = weeks_data.get(week, {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "work_signups": 0, "payers": 0, "vbb_value": 0, "agents_created": 0})
                rows.append({
                    "spend": round(d["spend"], 2),
                    "imp": d["imp"],
                    "clicks": d["clicks"],
                    "signups": round(d["signups"], 1),
                    "work_signups": round(d["work_signups"], 1),
                    "payers": int(round(d["payers"])),
                    "vbb_value": round(d["vbb_value"], 2),
                    "agents_created": round(d["agents_created"], 1),
                    "week": week,
                })
            output[geo] = rows
        results[brand_key] = json.dumps(output, separators=(",", ":"))

    return results["brand"], results["nonbrand"]


def update_geo_in_html(brand_json: str, nonbrand_json: str):
    """Replace or insert GEO_DATA constants in index.html."""
    content = INDEX_HTML.read_text()

    brand_line = f'const GEO_DATA_BRAND = {brand_json};'
    nonbrand_line = f'const GEO_DATA_NONBRAND = {nonbrand_json};'

    # Try to replace existing
    pat_brand = r'const GEO_DATA_BRAND = \{.*?\};'
    pat_nonbrand = r'const GEO_DATA_NONBRAND = \{.*?\};'

    new_content, count_b = re.subn(pat_brand, brand_line, content, count=1, flags=re.DOTALL)
    new_content, count_nb = re.subn(pat_nonbrand, nonbrand_line, new_content, count=1, flags=re.DOTALL)

    if count_b == 0 or count_nb == 0:
        # Insert before the geo report script section
        marker = '// ── GEO_DATA_MARKER ──'
        if marker in new_content:
            new_content = new_content.replace(marker, f'{brand_line}\n{nonbrand_line}\n{marker}')
        else:
            print("WARNING: Could not find GEO_DATA or marker in index.html. Appending before </script>.")
            # Find the last </script> and insert before it
            last_script_end = new_content.rfind('</script>')
            if last_script_end > 0:
                new_content = new_content[:last_script_end] + f'\n{brand_line}\n{nonbrand_line}\n' + new_content[last_script_end:]

    INDEX_HTML.write_text(new_content)
    print(f"\nUpdated index.html with GEO data (brand: {len(brand_json):,} chars, nonbrand: {len(nonbrand_json):,} chars)")


def main_geo():
    """Geo-only refresh."""
    print("🌍 Geo Report Refresh")
    print("=" * 50)

    geo_data = pull_geo_data()
    geo_data = compute_geo_aggregates(geo_data)

    # Summary
    for bk in ("brand", "nonbrand"):
        geos = [g for g in geo_data[bk] if g != "All Geos"]
        total_spend = sum(sum(w["spend"] for w in weeks.values()) for geo, weeks in geo_data[bk].items() if geo != "All Geos")
        print(f"\n📊 {bk}: {len(geos)} geos, ${total_spend:,.0f} total spend")

    brand_json, nonbrand_json = format_geo_data_for_html(geo_data)
    update_geo_in_html(brand_json, nonbrand_json)
    git_commit_push()
    print("\n✅ Geo data refreshed!")


if __name__ == "__main__":
    if "--geo-only" in sys.argv:
        main_geo()
    else:
        # Always refresh both cluster + geo reports together
        main()
        main_geo()

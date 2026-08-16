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
# Payers         = "Paying (MCC)"                           ctID 241978033
# Agents Created = "Agent Created (MCC)"                    ctID 7638407984
# VBB            = "VBB - HT prod - offline conversions"    ctID 7277286158
CONV_ACTIONS = {
    "Hard Signup (MCC)": "signups",
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
    ("agent_aiwork_builder", "Work Builder (agent)", "startswith"),
    ("agent_aiwork_process", "Agent - Work Process", "startswith"),
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

    # Structure: {cluster: {week: {spend, imp, clicks, signups, payers, vbb_value, agents_created}}}
    cluster_data = defaultdict(lambda: defaultdict(lambda: {
        "spend": 0, "imp": 0, "clicks": 0, "signups": 0, "payers": 0, "vbb_value": 0, "agents_created": 0
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

    # All cluster
    for week in sorted(all_weeks):
        totals = {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "payers": 0, "vbb_value": 0, "agents_created": 0}
        for cluster_name, weeks in cluster_data.items():
            if cluster_name in ("All", "All exc. Brand"):
                continue
            if week in weeks:
                for k in totals:
                    totals[k] += weeks[week][k]
        cluster_data["All"][week] = totals

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
            d = weeks_data.get(week, {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "payers": 0, "vbb_value": 0, "agents_created": 0})
            rows.append({
                "spend": round(d["spend"], 2),
                "imp": d["imp"],
                "clicks": d["clicks"],
                "signups": round(d["signups"], 1),
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


if __name__ == "__main__":
    main()

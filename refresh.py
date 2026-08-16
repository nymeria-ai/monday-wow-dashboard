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

# Conversion action IDs — LOCKED definitions (do NOT change without Tal's approval)
# All use ctID-based filtering via segments.conversion_action resource name.
# Hard Signups  = ctID 402542787
# Payers        = ctID 241978033
# Agents Created = ctID 7638407984
# VBB           = ctID 7277286158
CONV_ACTION_IDS = {
    "hard_signup": "402542787",
    "payer": "241978033",
    "agents_created": "7638407984",
    "vbb": "7277286158",
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

# ── Cluster value → Dashboard cluster name ──
# Position 5 (long format) or position 2 (short format)
CLUSTER_MAP = {
    # Brand
    "brand": "Brand",
    "brand_lt": "Brand",
    "brands_t": "Brand",
    # Competitors
    "comp1": "Competitors",
    "comp_asana": "Competitors",
    "comp_basecamp": "Competitors",
    "comp_clickup": "Competitors",
    "comp_comp2": "Competitors",
    "comp_exp": "Competitors",
    "comp_microsoft_pm": "Competitors",
    "comp_microsoft_teams": "Competitors",
    "comp_smartsheet": "Competitors",
    "comp_trello": "Competitors",
    "comp_jira": "Competitors",
    # Project
    "project_management": "Project",
    "project_management_free": "Project",
    "project_management_lt": "Project",
    "project_templates": "Project",
    "project_tracking": "Project",
    "projectgen": "Project",
    "pm_comparison": "Project",
    "pm_free": "Project",
    "pm_tool": "Project",
    # Task
    "task_management": "Task",
    "taskgen": "Task",
    # Gantt
    "gantt": "Gantt",
    "gantt_free": "Gantt",
    "gantt_template": "Gantt",
    "timeline": "Gantt",
    # Calendar
    "calendar": "Calendar",
    "shared_calendar": "Calendar",
    # To Do
    "to_do_list": "To Do",
    "checklist": "To Do",
    # General
    "general": "General",
    "management": "General",
    # Marketing
    "marketing": "Marketing",
    "marketing_templates": "Marketing",
    "marketing_calendar": "Marketing",
    "social_media": "Marketing",
    "content_calendar": "Marketing",
    # Logistics
    "logistics": "Logistics",
    "order_mgmt": "Logistics",
    "production_gen": "Logistics",
    # Agent clusters
    "agent_aihr": "Agent - HR",
    "agent_aifinance": "Agent - Finance",
    "agent_aiit": "Agent - IT",
    "agent_ailegal": "Agent - Legal",
    "agent_ailegal_contract": "Agent - Legal",
    "agent_ainote_taker": "Agent - Note Taker",
    "agent_aireal_estate": "Agent - Real Estate",
    "agent_aiwork_builder": "Work Builder (agent)",
    "agent_aiwork_process": "Agent - Work Process",
    "agent_aiconstruction": "Agent - Construction",
    "agent_aimarketing": "Agent - Marketing",
    "agent_aigeneric": "Agent - Generic",
    "agent_aicomp": "Agent - Comp",
    # Other / misc
    "workflow": "General",
    "kanban": "Competitors",
    "dashboards": "General",
    "schedule": "Other",
    "planner": "Other",
    "team": "Other",
    "tracker": "Other",
    "templates": "Other",
    "all_categories": "Other",
    "construction_management": "Logistics",
    "construction_gmao": "Logistics",
    "schedule": "Calendar",
    "task_free": "Task",
    "kanban": "Competitors",
    "email_marketing": "Marketing",
    # Tech
    "tech": "Tech",
}

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

    # Map cluster value to dashboard name
    if cluster_val in CLUSTER_MAP:
        mapped = CLUSTER_MAP[cluster_val]
        # WW region: non-Brand/non-Competitors clusters → WW
        if region == "ww" and mapped not in ("Brand", "Competitors"):
            return "WW"
        return mapped

    # Fallback: try prefix matching for comp_ patterns
    if cluster_val.startswith("comp_"):
        return "Competitors"
    if cluster_val.startswith("agent_ai"):
        return "Other"  # Unknown agent type

    # WW region fallback: anything not brand/comp → WW
    if region == "ww":
        return "WW"

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

        # Query 2: Conversions — all 4 actions by ctID (use all_conversions — secondary actions)
        conv_action_filter = " OR ".join(
            f"segments.conversion_action = 'customers/{acct_id}/conversionActions/{cid}'"
            for cid in CONV_ACTION_IDS.values()
        )
        conv_query = (
            f"SELECT campaign.name, segments.date, "
            f"segments.conversion_action, metrics.all_conversions, metrics.all_conversions_value "
            f"FROM campaign "
            f"WHERE segments.date BETWEEN '{START_DATE}' AND '{end_date}' "
            f"AND campaign.advertising_channel_type = 'SEARCH' "
            f"AND ({conv_action_filter})"
        )
        print(f"  Pulling conversion metrics (all 4 actions by ctID)...")
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

        # Process conversion rows (all 4 actions unified)
        for row in conv_rows:
            camp_name = row.get("campaign", {}).get("name", "")
            date = row.get("segments", {}).get("date", "")
            conv_action = row.get("segments", {}).get("conversionAction", "")
            metrics = row.get("metrics", {})

            cluster = extract_cluster(camp_name, acct_id)
            if cluster is None:
                continue

            week = week_start_wed(date)
            conversions = float(metrics.get("allConversions", 0))
            conv_value = float(metrics.get("allConversionsValue", 0))

            # Match by ctID suffix in the resource name
            if conv_action.endswith(f"/{CONV_ACTION_IDS['hard_signup']}"):
                cluster_data[cluster][week]["signups"] += conversions
            elif conv_action.endswith(f"/{CONV_ACTION_IDS['payer']}"):
                cluster_data[cluster][week]["payers"] += conversions
            elif conv_action.endswith(f"/{CONV_ACTION_IDS['agents_created']}"):
                cluster_data[cluster][week]["agents_created"] += conversions
            elif conv_action.endswith(f"/{CONV_ACTION_IDS['vbb']}"):
                cluster_data[cluster][week]["vbb_value"] += conv_value

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

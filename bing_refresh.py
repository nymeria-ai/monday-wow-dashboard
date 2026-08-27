#!/usr/bin/env python3
"""
Bing WoW Dashboard Refresh Script
Pulls data from 3 Microsoft Ads (Bing) accounts via the Reporting API,
maps campaigns to clusters using the same logic as refresh.py,
aggregates by Wed-Tue weeks, and updates the BING_DATA constant in index.html.

⚠️ MANDATORY: After ANY structural change to this script, index.html,
or refresh.py (new metrics, clusters, tabs, columns, filters, etc.),
update dashboard-spec.md to reflect the change. The spec is the single
source of truth for rebuilding/understanding the dashboard.

Accounts:
  - dapulse: 50033985
  - Monday.com - Big 4: 135096643
  - Monday.com - Locals: 135096648
  Customer ID: 21132515

Conversion Goals:
  - Hard Signups: Goal ID 20117320
  - Work Signups: Goal ID 31008558
"""
import json
import csv
import io
import os
import re
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

from bingads.service_client import ServiceClient
from bingads.authorization import AuthorizationData, OAuthWebAuthCodeGrant, OAuthTokens
from bingads.v13.reporting import ReportingServiceManager, ReportingDownloadParameters, time

SCRIPT_DIR = Path(__file__).parent
INDEX_HTML = SCRIPT_DIR / "index.html"
SECRETS_DIR = Path.home() / ".openclaw" / "workspace" / ".secrets"

# Bing Ads accounts
ACCOUNTS = {
    "50033985": "dapulse",
    "135096643": "Monday.com - Big 4",
    "135096648": "Monday.com - Locals",
}
CUSTOMER_ID = "21132515"

# Conversion Goal IDs (multiple goals accumulated per metric)
HARD_SIGNUPS_GOAL_IDS = {"20117320", "31018720"}
WORK_SIGNUPS_GOAL_IDS = {"31008558", "31018719"}

# Start date for historical data
START_DATE = "2026-06-01"

# ── Campaign exclusions — same as Google ──
CAMPAIGN_EXCLUSIONS = {"crm", "service", "globster", "elevate", "taka"}

# ── Geo-based cluster mapping ──
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

# ── Keyword → Cluster mapping ──
KEYWORD_CLUSTERS = [
    ("agent_aihr", "Agent - HR", "startswith"),
    ("agent_aifinance", "Agent - Finance", "startswith"),
    ("agent_aiit", "Agent - IT", "startswith"),
    ("agent_ailegal", "Agent - Legal", "startswith"),
    ("agent_ainote", "Agent - Note Taker", "startswith"),
    ("agent_aireal", "Agent - Real Estate", "startswith"),
    ("agent_aiwork_builder", "Agent - Work Agent", "startswith"),
    ("agent_aiwork_agent", "Agent - Work Agent", "startswith"),
    ("agent_aipmo_work_process", "Agent - Work Process", "startswith"),
    ("agent_aiwork_process", "Agent - Work Process", "startswith"),
    ("agent_aipmo", "Agent - PMO", "startswith"),
    ("agent_aiconstruction", "Agent - Construction", "startswith"),
    ("agent_aimarketing", "Agent - Marketing", "startswith"),
    ("agent_aigeneric", "Agent - Generic", "startswith"),
    ("agent_aicomp", "Agent - Comp", "startswith"),
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
    ("general", "General", "startswith"),
    ("workflow", "General", "exact"),
    ("dashboards", "General", "exact"),
    ("kanban", "Competitors", "exact"),
    ("tech", "Tech", "exact"),
    ("planner", "Other", "exact"),
    ("team", "Other", "exact"),
    ("tracker", "Other", "exact"),
    ("templates", "Other", "exact"),
    ("all_categories", "Other", "exact"),
]


def match_keyword_cluster(cluster_val: str) -> str | None:
    for kw, cluster_name, mode in KEYWORD_CLUSTERS:
        if mode == "startswith" and cluster_val.startswith(kw):
            return cluster_name
        elif mode == "exact" and cluster_val == kw:
            return cluster_name
        elif mode == "contains" and kw in cluster_val:
            return cluster_name
    return None


def extract_cluster(campaign_name: str, account_id: str) -> str | None:
    """Extract dashboard cluster from campaign name. Returns None to skip.
    Comp/Brand always wins over geo (geo clusters are 'generic' = excl. comp)."""
    base_name = campaign_name.split(" ")[0] if " " in campaign_name else campaign_name
    parts = base_name.split("-")
    parts_lower = [p.lower() for p in parts]

    # Exclusions
    if any(excl in p for p in parts_lower for excl in CAMPAIGN_EXCLUSIONS):
        return None
    if any(p in ("lead_management", "account_management", "lead_agent") for p in parts_lower):
        return None

    # Brand takes priority over everything
    if any(p == "brand" or p.startswith("brand_") or p == "brands_t" for p in parts_lower):
        return "Brand"
    # Comp takes priority over geo
    if any(p.startswith("comp") for p in parts_lower):
        return "Competitors"

    if len(parts) < 3:
        return "Other"

    # EU1 detection
    if any(p.lower() == "eu1" for p in parts):
        return "EU Generic"

    region = parts[0].lower()

    # Parse cluster_val
    if len(parts) >= 6 and parts[2].lower() == "prm":
        cluster_val = parts[5].lower()
    elif len(parts) >= 3 and parts[1].lower() == "s":
        cluster_val = parts[2].lower()
    else:
        if region in GEO_CLUSTERS:
            return GEO_CLUSTERS[region] + " Generic"
        return "Other"

    if "crm" in cluster_val:
        return None

    if cluster_val in ("ai", "max"):
        geo = GEO_CLUSTERS.get(region)
        return (geo + " Generic") if geo else "Other"

    # Geo-based mapping (generic = excl. comp)
    if region in GEO_CLUSTERS:
        return GEO_CLUSTERS[region] + " Generic"

    if region == "ww":
        return "WW"

    matched = match_keyword_cluster(cluster_val)
    if matched:
        return matched

    if cluster_val.startswith("comp_"):
        return "Competitors"
    if cluster_val.startswith("agent_ai"):
        return "Other"

    return "Other"


def week_start_wed(date_str: str) -> str:
    """Convert YYYY-MM-DD to the Wednesday that starts its Wed-Tue week."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    wednesday = d - timedelta(days=(d.weekday() - 2) % 7)
    return wednesday.strftime("%Y-%m-%d")


def read_secret(filename: str) -> str:
    """Read a secret file and return its stripped contents."""
    return (SECRETS_DIR / filename).read_text().strip()


def get_auth():
    """Create OAuth auth object with refresh token using tenant-specific endpoint."""
    client_id = read_secret("microsoft-ads-client-id.md")
    client_secret = read_secret("microsoft-ads-client-secret.md")
    developer_token = read_secret("microsoft-ads-developer-token.md")
    refresh_token = read_secret("microsoft-ads-refresh-token.md")
    tenant_id = read_secret("microsoft-ads-tenant-id.md")

    auth = OAuthWebAuthCodeGrant(
        client_id=client_id,
        client_secret=client_secret,
        redirection_uri="https://login.microsoftonline.com/common/oauth2/nativeclient",
        oauth_tokens=OAuthTokens(
            access_token=None,
            access_token_expires_in_seconds=0,
            refresh_token=refresh_token,
        ),
        tenant=tenant_id,
    )
    auth.request_oauth_tokens_by_refresh_token(refresh_token)
    return auth, developer_token


def download_report(reporting_manager, report_request):
    """Submit and download a report, return parsed CSV rows."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        params = ReportingDownloadParameters(
            report_request=report_request,
            result_file_directory=tmp_dir,
            result_file_name="report.csv",
            overwrite_result_file=True,
            timeout_in_milliseconds=300000,
        )
        result_file_path = reporting_manager.download_file(params)
        if result_file_path is None:
            return []

        # Handle zip file
        if result_file_path.endswith('.zip'):
            with zipfile.ZipFile(result_file_path, 'r') as zf:
                csv_names = [n for n in zf.namelist() if n.endswith('.csv')]
                if not csv_names:
                    return []
                content = zf.read(csv_names[0]).decode('utf-8-sig')
        else:
            with open(result_file_path, 'r', encoding='utf-8-sig') as f:
                content = f.read()

        # Parse CSV — skip Bing report header lines
        lines = content.strip().split('\n')
        # Find the header row (contains actual column names, not report metadata)
        header_idx = None
        for i, line in enumerate(lines):
            # Header row must contain a known column delimiter pattern
            if ('"CampaignName"' in line or '"TimePeriod"' in line) and '"Report' not in line:
                header_idx = i
                break
        if header_idx is None:
            # Fallback: look for tab-separated or unquoted headers
            for i, line in enumerate(lines):
                if 'CampaignName' in line and ('Spend' in line or 'GoalId' in line):
                    header_idx = i
                    break
        if header_idx is None:
            print(f"  WARNING: Could not find header row in report", file=sys.stderr)
            return []

        data_lines = lines[header_idx:]
        # Filter out summary/footer rows
        data_lines = [l for l in data_lines if l.strip() and not l.startswith('©') and not l.startswith('Report')]
        reader = csv.DictReader(io.StringIO('\n'.join(data_lines)))
        return list(reader)


def pull_data():
    """Pull performance and conversion data from all 3 Bing accounts."""
    auth, developer_token = get_auth()
    today = datetime.now()
    end_date = today - timedelta(days=1)

    print(f"Pulling Bing data from {START_DATE} to {end_date.strftime('%Y-%m-%d')}")

    # Structure: {cluster: {week: {spend, imp, clicks, signups, work_signups}}}
    cluster_data = defaultdict(lambda: defaultdict(lambda: {
        "spend": 0, "imp": 0, "clicks": 0, "signups": 0, "work_signups": 0
    }))


    for acct_id, acct_name in ACCOUNTS.items():
        print(f"\n=== {acct_name} ({acct_id}) ===")

        auth_data = AuthorizationData(
            account_id=acct_id,
            customer_id=CUSTOMER_ID,
            developer_token=developer_token,
            authentication=auth,
        )

        reporting_manager = ReportingServiceManager(
            authorization_data=auth_data,
            poll_interval_in_milliseconds=5000,
            environment='production',
        )
        report_svc = ServiceClient(
            service='ReportingService',
            version=13,
            authorization_data=auth_data,
            environment='production',
        )

        # ── Report 1: Campaign Performance (spend, impressions, clicks) ──
        print("  Pulling campaign performance report...")
        perf_request = report_svc.factory.create('CampaignPerformanceReportRequest')
        perf_request.ReportName = 'CampaignPerf'
        perf_request.Format = 'Csv'
        perf_request.ReturnOnlyCompleteData = False
        perf_request.Aggregation = 'Daily'

        # Columns
        perf_cols = report_svc.factory.create('ArrayOfCampaignPerformanceReportColumn')
        for col in ['CampaignName', 'AccountId', 'TimePeriod', 'Spend', 'Impressions', 'Clicks', 'CampaignType']:
            perf_cols.CampaignPerformanceReportColumn.append(col)
        perf_request.Columns = perf_cols

        # Scope
        scope = report_svc.factory.create('AccountThroughCampaignReportScope')
        account_ids = report_svc.factory.create('ns1:ArrayOflong')
        account_ids.long.append(int(acct_id))
        scope.AccountIds = account_ids
        scope.Campaigns = None
        perf_request.Scope = scope

        # Time
        perf_time = report_svc.factory.create('ReportTime')
        perf_time.CustomDateRangeStart = report_svc.factory.create('Date')
        start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
        perf_time.CustomDateRangeStart.Year = start_dt.year
        perf_time.CustomDateRangeStart.Month = start_dt.month
        perf_time.CustomDateRangeStart.Day = start_dt.day
        perf_time.CustomDateRangeEnd = report_svc.factory.create('Date')
        perf_time.CustomDateRangeEnd.Year = end_date.year
        perf_time.CustomDateRangeEnd.Month = end_date.month
        perf_time.CustomDateRangeEnd.Day = end_date.day
        perf_request.Time = perf_time

        # Filter: Search campaigns only
        perf_filter = report_svc.factory.create('CampaignPerformanceReportFilter')
        perf_request.Filter = perf_filter

        perf_rows = download_report(reporting_manager, perf_request)
        print(f"  Got {len(perf_rows)} performance rows")

        # Process performance rows
        for row in perf_rows:
            camp_name = row.get('CampaignName') or ''
            date_str = (row.get('TimePeriod') or '').split('T')[0]
            spend = float(row.get('Spend') or 0)
            imps = int(float(row.get('Impressions') or 0))
            clicks = int(float(row.get('Clicks') or 0))
            campaign_type = row.get('CampaignType') or ''

            # Only Search campaigns (Bing uses 'Search & content' type)
            if campaign_type and 'Search' not in campaign_type and 'search' not in campaign_type.lower():
                continue

            cluster = extract_cluster(camp_name, acct_id)
            if cluster is None:
                continue

            if not date_str:
                continue

            week = week_start_wed(date_str)
            cluster_data[cluster][week]["spend"] += spend
            cluster_data[cluster][week]["imp"] += imps
            cluster_data[cluster][week]["clicks"] += clicks

        # ── Report 2: Goals and Funnels (conversions by Goal ID) ──
        print("  Pulling goals & funnels report...")
        conv_request = report_svc.factory.create('GoalsAndFunnelsReportRequest')
        conv_request.ReportName = 'GoalsFunnels'
        conv_request.Format = 'Csv'
        conv_request.ReturnOnlyCompleteData = False
        conv_request.Aggregation = 'Daily'

        conv_cols = report_svc.factory.create('ArrayOfGoalsAndFunnelsReportColumn')
        for col in ['TimePeriod', 'AccountName', 'AccountId', 'CampaignName', 'GoalId', 'Goal', 'AllConversions']:
            conv_cols.GoalsAndFunnelsReportColumn.append(col)
        conv_request.Columns = conv_cols

        # Scope — GoalsAndFunnels requires AccountThroughAdGroupReportScope
        conv_scope = report_svc.factory.create('AccountThroughAdGroupReportScope')
        conv_account_ids = report_svc.factory.create('ns1:ArrayOflong')
        conv_account_ids.long.append(int(acct_id))
        conv_scope.AccountIds = conv_account_ids
        conv_scope.Campaigns = None
        conv_scope.AdGroups = None
        conv_request.Scope = conv_scope

        # Time (same range) — must explicitly null out PredefinedTime/ReportTimeZone
        conv_time = report_svc.factory.create('ReportTime')
        conv_time.PredefinedTime = None
        conv_time.ReportTimeZone = None
        conv_start = report_svc.factory.create('Date')
        conv_start.Year = start_dt.year
        conv_start.Month = start_dt.month
        conv_start.Day = start_dt.day
        conv_time.CustomDateRangeStart = conv_start
        conv_end = report_svc.factory.create('Date')
        conv_end.Year = end_date.year
        conv_end.Month = end_date.month
        conv_end.Day = end_date.day
        conv_time.CustomDateRangeEnd = conv_end
        conv_request.Time = conv_time

        # No filter — we'll filter by GoalId in code to avoid SOAP issues
        conv_request.Filter = None

        conv_rows = download_report(reporting_manager, conv_request)
        print(f"  Got {len(conv_rows)} conversion rows")

        for row in conv_rows:
            camp_name = row.get('CampaignName', '') or ''
            date_str = (row.get('TimePeriod', '') or '').split('T')[0]
            goal_id = str(row.get('GoalId', '') or '')
            conversions = float(row.get('AllConversions', 0) or 0)

            if not camp_name or not date_str:
                continue

            cluster = extract_cluster(camp_name, acct_id)
            if cluster is None:
                continue

            week = week_start_wed(date_str)

            if goal_id in HARD_SIGNUPS_GOAL_IDS:
                cluster_data[cluster][week]["signups"] += conversions
            elif goal_id in WORK_SIGNUPS_GOAL_IDS:
                cluster_data[cluster][week]["work_signups"] += conversions

    return cluster_data


def compute_aggregates(cluster_data: dict) -> dict:
    """Compute 'All' and 'All Generic' aggregates."""
    all_weeks = set()
    for weeks in cluster_data.values():
        all_weeks.update(weeks.keys())

    EXCLUDED_FROM_GENERIC = {"All", "All exc. Brand", "All Generic", "Brand", "Competitors", "Agent - Work Agent"}

    for week in sorted(all_weeks):
        totals = {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "work_signups": 0}
        generic_totals = {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "work_signups": 0}
        for cluster_name, weeks in cluster_data.items():
            if cluster_name in ("All", "All exc. Brand", "All Generic"):
                continue
            if week in weeks:
                for k in totals:
                    totals[k] += weeks[week][k]
                if cluster_name not in EXCLUDED_FROM_GENERIC and not cluster_name.startswith("Agent"):
                    for k in generic_totals:
                        generic_totals[k] += weeks[week][k]
        cluster_data["All"][week] = totals
        cluster_data["All Generic"][week] = generic_totals

    return cluster_data


def format_data_for_html(cluster_data: dict) -> str:
    """Format cluster data as the JavaScript BING_DATA constant."""
    all_weeks = set()
    for weeks in cluster_data.values():
        all_weeks.update(weeks.keys())
    sorted_weeks = sorted(all_weeks)

    output = {}
    for cluster_name in sorted(cluster_data.keys()):
        if cluster_name == "All exc. Brand":
            continue
        weeks_data = cluster_data[cluster_name]
        rows = []
        for week in sorted_weeks:
            d = weeks_data.get(week, {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "work_signups": 0})
            rows.append({
                "spend": round(d["spend"], 2),
                "imp": d["imp"],
                "clicks": d["clicks"],
                "signups": round(d["signups"], 1),
                "work_signups": round(d["work_signups"], 1),
                "week": week,
            })
        output[cluster_name] = rows

    return json.dumps(output, separators=(",", ":"))


def update_index_html(data_json: str):
    """Replace or insert the BING_DATA constant in index.html."""
    content = INDEX_HTML.read_text()

    bing_line = f'const BING_DATA = {data_json};'
    pattern = r'const BING_DATA = \{.*?\};'

    new_content, count = re.subn(pattern, bing_line, content, count=1, flags=re.DOTALL)
    if count == 0:
        # Insert before the GEO_DATA_MARKER
        marker = '// ── GEO_DATA_MARKER ──'
        if marker in new_content:
            new_content = new_content.replace(marker, f'{bing_line}\n{marker}')
        else:
            # Fallback: insert before last </script>
            last_script_end = new_content.rfind('</script>')
            if last_script_end > 0:
                new_content = new_content[:last_script_end] + f'\n{bing_line}\n' + new_content[last_script_end:]

    INDEX_HTML.write_text(new_content)
    print(f"\nUpdated index.html with BING_DATA ({len(data_json):,} chars)")


def git_commit_push():
    """Commit and push changes."""
    import subprocess as sp
    today = datetime.now().strftime("%Y-%m-%d")
    sp.run(["git", "add", "index.html", "bing_refresh.py"], cwd=SCRIPT_DIR, check=True)
    result = sp.run(["git", "diff", "--cached", "--quiet"], cwd=SCRIPT_DIR)
    if result.returncode == 0:
        print("No changes to commit")
        return False
    sp.run(["git", "commit", "-m", f"Add Bing WoW tab + data {today}"], cwd=SCRIPT_DIR, check=True)
    sp.run(["git", "push"], cwd=SCRIPT_DIR, check=True)
    print(f"Committed and pushed: Add Bing WoW tab + data {today}")
    return True


def main():
    print("🔄 Bing WoW Dashboard Refresh")
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
        print("\n✅ Bing dashboard refreshed and deployed!")
    else:
        print("\n✅ No new data to deploy")


if __name__ == "__main__":
    main()

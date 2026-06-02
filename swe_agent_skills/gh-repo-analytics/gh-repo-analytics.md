---
name: gh-repo-analytics
description: Analyze GitHub repository activity using the gh CLI and GitHub REST API to produce structured JSON reports covering PRs and issues for a given time window.
---

## Overview

Use the `gh` CLI (authenticated via `GH_TOKEN`) to query pull requests and issues from a GitHub repository for a specific date range, then produce a JSON report.

## Authentication

The `GH_TOKEN` environment variable is set. The `gh` CLI uses it automatically. Verify access with:

```bash
gh auth status
```

## Fetching Pull Requests

Use `gh pr list` with `--search` to filter by creation date and `--json` to get structured output:

```bash
gh pr list \
  --repo <owner>/<repo> \
  --state all \
  --search "created:2024-12-01..2024-12-31" \
  --limit 200 \
  --json number,title,state,createdAt,mergedAt,author
```

Key fields:
- `state`: `OPEN`, `CLOSED`, or `MERGED`
- `mergedAt`: non-null only for merged PRs
- `createdAt`: creation timestamp (ISO 8601)
- `author.login`: GitHub username

**Important:** `--state all` returns open+closed; merged PRs have `state=MERGED` or check `mergedAt != null`. Use `--limit` high enough to get all results; paginate if needed with `--limit 1000` or loop.

### Computing avg_merge_days

For each merged PR:
```
merge_days = (mergedAt - createdAt).total_seconds() / 86400
```
Average across all merged PRs, round to 1 decimal place.

### Top contributor

Count PRs per `author.login`, take the login with the highest count.

## Fetching Issues

Use `gh issue list` similarly:

```bash
gh issue list \
  --repo <owner>/<repo> \
  --state all \
  --search "created:2024-12-01..2024-12-31" \
  --limit 1000 \
  --json number,title,state,labels,createdAt,closedAt
```

Key fields:
- `labels`: array of `{name, ...}` objects
- `state`: `OPEN` or `CLOSED`

### Bug detection

A bug report is any issue where **at least one label name contains the substring `bug`** (case-insensitive check recommended):

```python
is_bug = any("bug" in label["name"].lower() for label in issue["labels"])
```

### resolved_bugs

Bug issues that have `state == "CLOSED"` (regardless of when they were closed — the task asks how many bug issues were closed, not necessarily within the month unless the task says so — read instructions carefully).

## Writing the Report

Save to `/app/report.json`:

```json
{
  "pr": {
    "total": <int>,
    "merged": <int>,
    "closed": <int>,
    "avg_merge_days": <float>,
    "top_contributor": <str>
  },
  "issue": {
    "total": <int>,
    "bug": <int>,
    "resolved_bugs": <int>
  }
}
```

- `pr.closed`: PRs with `state == "CLOSED"` (not merged, just closed without merge)
- `pr.merged`: PRs with `state == "MERGED"` or `mergedAt != null`
- `issue.total`: all issues created in the date window
- `issue.bug`: issues with a bug label
- `issue.resolved_bugs`: bug issues that are closed

## Recommended Approach

Write a short Python script that:
1. Calls `subprocess.run(["gh", "pr", "list", ...])` with `--json` and captures stdout
2. Parses JSON, computes all metrics
3. Calls `subprocess.run(["gh", "issue", "list", ...])` similarly
4. Writes `/app/report.json`

Or use shell + `jq` for lightweight processing.

## Pagination Pitfall

`gh pr list` defaults to 30 results. Always pass `--limit 500` (or higher) to avoid undercounting. If the repo is very active, loop with `--page` or use the GitHub REST API directly via `gh api` with pagination:

```bash
gh api --paginate repos/<owner>/<repo>/pulls \
  -f state=all \
  -f per_page=100 \
  --jq '.[] | select(.created_at >= "2024-12-01" and .created_at < "2025-01-01")'
```

## Timezone Note

GitHub timestamps are UTC. Treat the date range as UTC boundaries: `>= 2024-12-01T00:00:00Z` and `< 2025-01-01T00:00:00Z`.

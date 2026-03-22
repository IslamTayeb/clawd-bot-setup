#!/bin/bash
# Parameter Golf Competition Tracker
# Pulls leaderboard, open PRs, and issues from openai/parameter-golf
# Also updates the vault tracker .md file

REPO="openai/parameter-golf"
OUT_DIR="/tmp/parameter-golf-data"
VAULT_TRACKER="/home/ec2-user/obsidian-vault/research/parameter-golf-tracker.md"
mkdir -p "$OUT_DIR"

echo "=== LEADERBOARD (README) ==="
gh api repos/$REPO/readme --jq '.content' | python3 -c "import sys,base64; print(base64.b64decode(sys.stdin.read().replace('\n','')).decode())" > "$OUT_DIR/readme.md" 2>/dev/null
cat "$OUT_DIR/readme.md"

echo ""
echo "=== OPEN PRs ==="
gh pr list --repo $REPO --state open --limit 50 --json number,title,author,createdAt,body,comments --jq '.[] | "PR #\(.number) by \(.author.login) [\(.createdAt[:10])]: \(.title)\nBody: \(.body[:500])\nComments: \(.comments | length)\n---"'

echo ""
echo "=== RECENT CLOSED/MERGED PRs (last 7 days) ==="
gh pr list --repo $REPO --state merged --limit 20 --json number,title,author,mergedAt,body --jq '.[] | "PR #\(.number) by \(.author.login) [merged \(.mergedAt[:10])]: \(.title)\nBody: \(.body[:300])\n---"'

echo ""
echo "=== OPEN ISSUES ==="
gh issue list --repo $REPO --state open --limit 20 --json number,title,author,createdAt,body,comments --jq '.[] | "Issue #\(.number) by \(.author.login) [\(.createdAt[:10])]: \(.title)\nBody: \(.body[:300])\nComments: \(.comments | length)\n---"'

echo ""
echo "=== DISCUSSIONS ==="
gh api graphql -f query='{ repository(owner:"openai", name:"parameter-golf") { discussions(first:15, orderBy:{field:CREATED_AT, direction:DESC}) { nodes { title author{login} createdAt body url category{name} comments{totalCount} } } } }' --jq '.data.repository.discussions.nodes[] | "[\(.category.name)] \(.title) by \(.author.login) [\(.createdAt[:10])]\nBody: \(.body[:300])\nComments: \(.comments.totalCount)\nURL: \(.url)\n---"' 2>/dev/null || echo "(No discussions endpoint or empty)"

echo ""
echo "=== UPDATING VAULT TRACKER ==="

# Fetch ALL open PRs with scores
python3 << 'PYEOF'
import subprocess, json, re, os, datetime

repo = "openai/parameter-golf"
vault_tracker = os.environ.get("VAULT_TRACKER", "/home/ec2-user/obsidian-vault/research/parameter-golf-tracker.md")

# Get all open PRs (paginated)
all_prs = []
for page in range(1, 6):
    r = subprocess.run(
        ["gh", "api", f"/repos/{repo}/pulls?state=open&per_page=100&page={page}&sort=created&direction=desc"],
        capture_output=True, text=True, timeout=30
    )
    if r.returncode != 0:
        break
    data = json.loads(r.stdout)
    if not data:
        break
    all_prs.extend(data)

total_prs = len(all_prs)
print(f"Fetched {total_prs} open PRs")

# Find highest PR number already in tracker
existing_max = 0
if os.path.exists(vault_tracker):
    with open(vault_tracker) as f:
        content = f.read()
    for m in re.finditer(r'#(\d+)', content):
        num = int(m.group(1))
        if num > existing_max:
            existing_max = num
print(f"Highest PR in tracker: #{existing_max}")

# Filter new PRs
new_prs = [pr for pr in all_prs if pr['number'] > existing_max]
if not new_prs:
    print("No new PRs to add")
    # Still update the total count and timestamp
    if os.path.exists(vault_tracker):
        with open(vault_tracker) as f:
            content = f.read()
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
        ts = now.strftime("%Y-%m-%d %I:%M %p ET")
        content = re.sub(
            r'Last updated:.*',
            f'Last updated: {ts} ({total_prs}-PR sweep, auto-updated)',
            content
        )
        with open(vault_tracker, 'w') as f:
            f.write(content)
else:
    print(f"New PRs to add: {len(new_prs)}")

    # Build new scored and unscored rows
    scored_rows = []
    unscored_rows = []
    
    for pr in sorted(new_prs, key=lambda x: x['number']):
        title = pr['title']
        num = pr['number']
        author = pr['user']['login']
        date = pr['created_at'][5:10]
        
        score_match = re.search(r'val_bpb[=:\s]*([0-9]+\.[0-9]+)', title)
        score = score_match.group(1) if score_match else None
        
        title_lower = title.lower()
        if 'non-record' in title_lower or 'non record' in title_lower:
            ptype = 'Non-record'
        elif 'record' in title_lower:
            ptype = 'Record'
        elif 'wip' in title_lower or 'draft' in title_lower:
            ptype = 'WIP'
        else:
            ptype = '—'
        
        summary = title[:90]
        
        if score:
            scored_rows.append(f"| — | {score} | #{num} | {author} | {summary} | {date} | {ptype} | — |")
        else:
            unscored_rows.append(f"| #{num} | {author} | {summary} | {date} | {ptype} | — |")
    
    # Build update section
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
    ts = now.strftime("%Y-%m-%d %I:%M %p ET")
    
    update = f"\n\n---\n\n## AUTO-UPDATE: {ts} — {len(new_prs)} new PRs (#{existing_max+1}-#{max(pr['number'] for pr in new_prs)})\n\n"
    update += f"Total open PRs: {total_prs}\n\n"
    
    if scored_rows:
        update += "### New Scored Entries\n\n"
        update += "| Rank | Score | PR | Author | Summary | Date | Type | Who |\n"
        update += "|------|-------|----|--------|---------|------|------|-----|\n"
        update += "\n".join(scored_rows) + "\n\n"
    
    if unscored_rows:
        update += "### New Unscored / WIP Entries\n\n"
        update += "| PR | Author | Summary | Date | Type | Who |\n"
        update += "|----|--------|---------|------|------|-----|\n"
        update += "\n".join(unscored_rows) + "\n\n"
    
    # Append to tracker
    with open(vault_tracker) as f:
        content = f.read()
    
    content = re.sub(
        r'Last updated:.*',
        f'Last updated: {ts} ({total_prs}-PR sweep, auto-updated)',
        content
    )
    
    content += update
    
    with open(vault_tracker, 'w') as f:
        f.write(content)
    
    print(f"Appended {len(scored_rows)} scored + {len(unscored_rows)} unscored rows")

# Git push
os.chdir("/home/ec2-user/obsidian-vault")
subprocess.run(["git", "add", "-A"], capture_output=True)
r = subprocess.run(["git", "commit", "-m", f"Auto-update tracker: {total_prs} PRs"], capture_output=True, text=True)
if "nothing to commit" not in r.stdout + r.stderr:
    subprocess.run(["git", "push"], capture_output=True)
    print("Pushed to git")
else:
    print("No changes to push")

PYEOF

#!/bin/bash
# Parameter Golf Competition Tracker
# Pulls leaderboard, open PRs, issues, and discussions
# APPENDS new PRs to vault tracker and pushes to git

REPO="openai/parameter-golf"
VAULT_TRACKER="/home/ec2-user/obsidian-vault/research/parameter-golf-tracker.md"

echo "=== LEADERBOARD (README) ==="
gh api repos/$REPO/readme --jq '.content' | python3 -c "import sys,base64; print(base64.b64decode(sys.stdin.read().replace('\n','')).decode())" 2>/dev/null

echo ""
echo "=== OPEN PRs (latest 50) ==="
gh pr list --repo $REPO --state open --limit 50 --json number,title,author,createdAt,comments --jq '.[] | "PR #\(.number) by \(.author.login) [\(.createdAt[:10])]: \(.title) (comments: \(.comments | length))\n---"'

echo ""
echo "=== OPEN ISSUES ==="
gh issue list --repo $REPO --state open --limit 20 --json number,title,author,createdAt,comments --jq '.[] | "Issue #\(.number) by \(.author.login) [\(.createdAt[:10])]: \(.title) (comments: \(.comments | length))\n---"'

echo ""
echo "=== UPDATING VAULT TRACKER (APPEND-ONLY) ==="

python3 << 'PYEOF'
import subprocess, json, re, os, datetime

repo = "openai/parameter-golf"
vault_tracker = "/home/ec2-user/obsidian-vault/research/parameter-golf-tracker.md"

if not os.path.exists(vault_tracker):
    print("ERROR: Tracker file not found!")
    exit(1)

# Read existing tracker and find highest PR number
with open(vault_tracker) as f:
    content = f.read()

existing_prs = set()
for m in re.finditer(r'#(\d+)', content):
    existing_prs.add(int(m.group(1)))
existing_max = max(existing_prs) if existing_prs else 0
print(f"Existing tracker: highest PR #{existing_max}, {len(existing_prs)} unique PRs")

# Get ALL open PRs with full pagination
all_prs = []
for page in range(1, 15):
    r = subprocess.run(
        ["gh", "api", f"/repos/{repo}/pulls?state=open&per_page=100&page={page}&sort=created&direction=desc"],
        capture_output=True, text=True, timeout=30
    )
    if r.returncode != 0: break
    data = json.loads(r.stdout)
    if not data: break
    all_prs.extend(data)

total_prs = len(all_prs)
print(f"Fetched {total_prs} open PRs")

# Find genuinely new PRs not in tracker
new_prs = sorted([pr for pr in all_prs if pr['number'] not in existing_prs], key=lambda x: x['number'])

if not new_prs:
    print("No new PRs to add")
    # Just update timestamp
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
    ts = now.strftime("%Y-%m-%d %I:%M %p ET")
    content = re.sub(r'Last updated:.*', f'Last updated: {ts} ({total_prs} open PRs, no new entries)', content)
    with open(vault_tracker, 'w') as f:
        f.write(content)
    exit(0)

print(f"New PRs to add: {len(new_prs)}")

# Parse new PRs
scored = []
unscored = []
for pr in new_prs:
    title = pr['title']
    num = pr['number']
    author = pr['user']['login']
    date = pr['created_at'][5:10]
    score_match = re.search(r'val_bpb[=:\s]*([0-9]+\.[0-9]+)', title)
    if not score_match:
        score_match = re.search(r'([0-9]\.[0-9]{3,})\s*(?:bpb|BPB)', title)
    if not score_match:
        candidates = re.findall(r'(\d+\.\d{3,})', title)
        for c in candidates:
            v = float(c)
            if 0.01 < v < 2.5:
                score_match = type('obj', (object,), {'group': lambda self, x=c: x})()
                break
    score = score_match.group(1) if score_match else None
    summary = title[:90].replace('|', '/')
    ptype = '—'
    if 'record' in title.lower() and 'non-record' not in title.lower():
        ptype = 'Record'
    elif 'non-record' in title.lower():
        ptype = 'Non-record'
    
    if score:
        scored.append((float(score), num, author, summary, date, ptype))
    else:
        unscored.append((num, author, summary, date, ptype))

scored.sort(key=lambda x: x[0])

# APPEND new entries to the EXISTING table (insert into sorted position)
# Find the table section
table_start = content.find("## All Submissions")
table_header_end = content.find("|------|", table_start)
if table_header_end == -1:
    print("ERROR: Could not find table header")
    exit(1)

# Find end of table (next ## section)
lines = content.split('\n')
table_lines = []
other_lines_before = []
other_lines_after = []
in_table = False
past_table = False
table_start_idx = None
table_end_idx = None

for i, line in enumerate(lines):
    if '## All Submissions' in line:
        in_table = True
        table_start_idx = i
    if in_table and not past_table:
        if line.startswith('## ') and 'All Submissions' not in line:
            past_table = True
            table_end_idx = i

# Extract existing table rows
existing_rows = []
for i in range(table_start_idx, table_end_idx):
    line = lines[i]
    if line.startswith('| ') and not line.startswith('| Rank') and not line.startswith('|---'):
        parts = [p.strip() for p in line.split('|')]
        if len(parts) >= 4:
            try:
                score = float(parts[2])
                existing_rows.append((score, line))
            except:
                existing_rows.append((999, line))

# Add new scored entries as rows
for score, num, author, summary, date, ptype in scored:
    row = f"| — | {score:.4f} | #{num} | {author} | {summary} | {date} | {ptype} | — |"
    existing_rows.append((score, row))

# Sort all rows
existing_rows.sort(key=lambda x: x[0])

# Rebuild table with re-numbered ranks
now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4)))
ts = now.strftime("%Y-%m-%d %I:%M %p ET")

new_table = f"""## All Submissions — Complete Ranked Table

Total open PRs: {total_prs} | Scored: {len(existing_rows)} | Unscored: varies
Last rebuilt: {ts}

| Rank | Score | PR | Author | Summary | Date | Type | Who |
|------|-------|----|--------|---------|------|------|-----|
"""

for i, (score, row) in enumerate(existing_rows, 1):
    # Re-rank: replace the rank field
    parts = [p.strip() for p in row.split('|')]
    if len(parts) >= 9:
        parts[1] = f" {i} "
        new_table += '|'.join(parts) + "\n"
    else:
        new_table += row + "\n"

# Replace table section in content
before_table = '\n'.join(lines[:table_start_idx])
after_table = '\n'.join(lines[table_end_idx:])

new_content = before_table + '\n' + new_table + '\n' + after_table

# Update timestamp
new_content = re.sub(r'Last updated:.*', f'Last updated: {ts} ({total_prs} open PRs, +{len(new_prs)} new)', new_content)

with open(vault_tracker, 'w') as f:
    f.write(new_content)

print(f"Done: added {len(scored)} scored + {len(unscored)} unscored. Table now has {len(existing_rows)} scored entries.")
PYEOF

# Git commit and push
cd /home/ec2-user/obsidian-vault
git add -A
CHANGED=$(git diff --cached --stat)
if [ -n "$CHANGED" ]; then
    git commit -m "Auto-update tracker: append new PRs"
    git push
    echo "Pushed to git"
else
    echo "No changes to push"
fi

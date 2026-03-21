#!/bin/bash
# Parameter Golf Competition Tracker
# Pulls leaderboard, open PRs, and issues from openai/parameter-golf

REPO="openai/parameter-golf"
OUT_DIR="/tmp/parameter-golf-data"
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

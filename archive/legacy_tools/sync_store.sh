#!/bin/bash
# Auto-sync fiam-code store to git repo
# Run via cron: */15 * * * * /home/fiet/fiam-code/scripts/sync_store.sh
set -e
cd /home/fiet/fiam-code

# Only sync if there are changes in store/
if git diff --quiet store/ && git diff --cached --quiet store/; then
    exit 0
fi

git add store/
git commit -m "auto: sync store $(date +%Y%m%d_%H%M)" --no-verify
git push origin feat/memory-graph 2>/dev/null || true

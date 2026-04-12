#!/usr/bin/env bash
# sync-store.sh — Bidirectional store sync between ISP and Local (via DO jump)
#
# Usage (from ISP):
#   bash sync-store.sh              # bidirectional (default)
#   bash sync-store.sh up           # ISP → Local (push new events to Local)
#   bash sync-store.sh down         # Local → ISP (pull from Local)
#
# Requires: rsync, ssh access ISP→DO→Local chain
#   ISP → DO: direct ssh root@209.38.69.231
#   DO  → Local: ssh -p 2222 Aurora@127.0.0.1 (via reverse tunnel)
#
# NOTE: "down" (Local→ISP) requires the reverse tunnel to be up.

set -euo pipefail

DIRECTION="${1:-both}"
LOCAL_STORE="/root/fiam-code/store/"
DO="root@209.38.69.231"

# For ISP→DO→Local, we need the reverse tunnel
# Local is reachable from ISP as: ssh -J $DO -p 2222 Aurora@127.0.0.1
# Or from DO as: ssh -p 2222 Aurora@127.0.0.1
# rsync needs the local store path on the Local machine (Windows path mapped)
# This is complex — for now, ISP pushes TO DO as staging, Local pulls from DO.

# Simpler: ISP ↔ DO staging area
DO_STAGING="/tmp/fiam-store-sync/"

RSYNC_FLAGS="-avz --progress"
RSYNC_EXCLUDE="--exclude cursor.json --exclude *.pid"

sync_up() {
    echo "[UP] ISP → DO staging"
    rsync $RSYNC_FLAGS $RSYNC_EXCLUDE "$LOCAL_STORE" "${DO}:${DO_STAGING}"
    echo "[UP] Done. Local should pull from DO: rsync -avz root@209.38.69.231:${DO_STAGING} f:/fiam-code/store/"
}

sync_down() {
    echo "[DOWN] DO staging → ISP"
    rsync $RSYNC_FLAGS $RSYNC_EXCLUDE "${DO}:${DO_STAGING}" "$LOCAL_STORE"
}

case "$DIRECTION" in
    up)   sync_up ;;
    down) sync_down ;;
    both) sync_up; sync_down ;;
    *)    echo "Usage: $0 [up|down|both]"; exit 1 ;;
esac

echo "[sync] Done."

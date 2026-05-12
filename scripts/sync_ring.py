#!/usr/bin/env python3
"""Read today's Colmi R02 ring data from a local SQLite DB and POST to the FIAM server.

Usage:
  python scripts/sync_ring.py --db <path/to/ring.db> [--server <url>] [--token <token>]

The script reads from the colmi_r02_client SQLite schema (heart_rates + sport_details
tables) and pushes a summary to POST /ring/sync on the dashboard server.

Environment variables (used if --server / --token are not provided):
  FIAM_API_BASE     base URL, e.g. https://fiet.cc
  FIAM_INGEST_TOKEN auth token
"""
import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path


def today_utc_range() -> tuple[str, str]:
    """Return UTC ISO strings for [start_of_today, end_of_today)."""
    today = date.today()
    start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc).isoformat()
    end = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc).isoformat()
    return start, end


def read_heart_rates(conn: sqlite3.Connection, start: str, end: str) -> list[tuple[str, int]]:
    """Return [(timestamp_iso, reading)] for today, ordered by time."""
    cur = conn.execute(
        "SELECT timestamp, reading FROM heart_rates WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
        (start, end),
    )
    return cur.fetchall()


def read_sport_details(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """Return list of {timestamp, steps, calories, distance} for today."""
    cur = conn.execute(
        "SELECT timestamp, steps, calories, distance FROM sport_details WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
        (start, end),
    )
    rows = cur.fetchall()
    return [{"timestamp": r[0], "steps": r[1], "calories": r[2], "distance": r[3]} for r in rows]


def build_payload(hr_rows: list[tuple[str, int]], sport_rows: list[dict]) -> dict:
    today_str = date.today().isoformat()
    payload: dict = {"date": today_str}

    # Heart rate processing
    if hr_rows:
        non_zero = [r for r in hr_rows if r[1] > 0]
        if non_zero:
            payload["current_hr"] = non_zero[-1][1]
            payload["max_hr"] = max(r[1] for r in non_zero)
            # Resting HR: average of readings before 07:00 UTC
            resting = [r[1] for r in non_zero if "T07:" not in r[0] and r[0][11:13] < "07"]
            if resting:
                payload["resting_hr"] = round(sum(resting) / len(resting))

        # Build hr_series with HH:MM labels
        hr_series = []
        for ts, reading in hr_rows:
            # ts is like "2026-05-11 08:30:00+00:00" or "2026-05-11T08:30:00+00:00"
            time_part = ts.replace("T", " ").split(" ")[1][:5]
            hr_series.append({"time": time_part, "hr": reading})
        payload["hr_series"] = hr_series

    # Steps processing
    if sport_rows:
        payload["steps"] = sum(r["steps"] for r in sport_rows)
        payload["calories"] = sum(r["calories"] for r in sport_rows)
        payload["distance_m"] = sum(r["distance"] for r in sport_rows)

    return payload


def post_to_server(payload: dict, server: str, token: str) -> dict:
    url = server.rstrip("/") + "/ring/sync"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "X-Fiam-Token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Colmi R02 ring data to FIAM server")
    parser.add_argument("--db", required=True, help="Path to colmi SQLite database")
    parser.add_argument("--server", default="", help="FIAM server base URL (or set FIAM_API_BASE)")
    parser.add_argument("--token", default="", help="FIAM ingest token (or set FIAM_INGEST_TOKEN)")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without sending")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        print(f"Error: DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    server = args.server or os.environ.get("FIAM_API_BASE", "").strip()
    token = args.token or os.environ.get("FIAM_INGEST_TOKEN", "").strip()
    if not server:
        print("Error: --server or FIAM_API_BASE required", file=sys.stderr)
        sys.exit(1)
    if not token and not args.dry_run:
        print("Error: --token or FIAM_INGEST_TOKEN required", file=sys.stderr)
        sys.exit(1)

    start, end = today_utc_range()
    conn = sqlite3.connect(str(db_path))
    try:
        hr_rows = read_heart_rates(conn, start, end)
        sport_rows = read_sport_details(conn, start, end)
    finally:
        conn.close()

    payload = build_payload(hr_rows, sport_rows)
    print(f"Today: {payload['date']}")
    print(f"  HR readings: {len(hr_rows)}, current={payload.get('current_hr')}, resting={payload.get('resting_hr')}, max={payload.get('max_hr')}")
    print(f"  Steps: {payload.get('steps')}, calories: {payload.get('calories')}, distance_m: {payload.get('distance_m')}")

    if args.dry_run:
        print("\nDry run — payload:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    result = post_to_server(payload, server, token)
    if result.get("ok"):
        print(f"Synced: {result}")
    else:
        print(f"Server error: {result}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

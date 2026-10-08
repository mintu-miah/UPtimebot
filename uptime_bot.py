#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
uptime_bot.py - Server uptime / downtime monitor for cPanel (stdlib only).

Usage:
    python3 uptime_bot.py check              # run one check (use this in cron, every minute)
    python3 uptime_bot.py report             # print report (last 24h, 7d, 30d)
    python3 uptime_bot.py report --days 14   # report for a custom window
    python3 uptime_bot.py report --email     # also email the report
    python3 uptime_bot.py incidents          # list recent down incidents
    python3 uptime_bot.py testalert          # send a test alert (email/Telegram)

Works on Python 3.6+.
"""

import argparse
import json
import os
import smtplib
import socket
import sqlite3
import ssl
import sys
import time
from email.mime.text import MIMEText
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ----------------------------------------------------------------------
# CONFIGURATION - edit this section
# ----------------------------------------------------------------------
CONFIG = {
    # What to monitor. Add as many targets as you like.
    "targets": [
        {
            "name": "Main website",
            "type": "http",                 # "http" or "tcp"
            "url": "https://example.com",   # <-- CHANGE THIS
            "timeout": 10,                  # seconds
            "ok_status_max": 399,           # status codes below/equal to this = UP
            "verify_ssl": True,
            # "must_contain": "Welcome",    # optional: text that must appear in page
        },
        # Example TCP check (database / mail / ssh port):
        # {"name": "MySQL port", "type": "tcp", "host": "example.com",
        #  "port": 3306, "timeout": 5},
    ],

    # A target is declared DOWN only after this many consecutive failed checks.
    # With cron every minute, 2 = ~1-2 minutes of confirmed failure (avoids false alarms).
    "fail_threshold": 2,

    # Each check retries this many times before counting as one failure.
    "retries_per_check": 2,

    "db_path": os.path.join(BASE_DIR, "uptime.db"),
    "keep_check_days": 90,      # raw check history kept this long (incidents kept forever)

    # Email alerts - use a cPanel email account (Email Accounts > Connect Devices)
    "email": {
        "enabled": False,
        "smtp_host": "mail.example.com",
        "smtp_port": 465,                    # 465 = SSL
        "username": "alerts@example.com",
        "password": "CHANGE_ME",
        "from": "alerts@example.com",
        "to": ["you@example.com"],
    },

    # Telegram alerts (optional, free). See manual for how to get these.
    "telegram": {
        "enabled": False,
        "bot_token": "123456:ABC-your-token",
        "chat_id": "123456789",
    },
}


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def now_ts():
    return int(time.time())


def fmt_time(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def fmt_duration(seconds):
    seconds = int(max(0, seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append("%dd" % d)
    if h:
        parts.append("%dh" % h)
    if m:
        parts.append("%dm" % m)
    if s or not parts:
        parts.append("%ds" % s)
    return " ".join(parts)


# ----------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------
def db_connect():
    conn = sqlite3.connect(CONFIG["db_path"], timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS checks (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               target TEXT NOT NULL,
               ts INTEGER NOT NULL,
               up INTEGER NOT NULL,
               status_code INTEGER,
               response_ms INTEGER,
               error TEXT)"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_checks_t ON checks(target, ts)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS incidents (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               target TEXT NOT NULL,
               start_ts INTEGER NOT NULL,
               end_ts INTEGER,
               reason TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS state (
               target TEXT PRIMARY KEY,
               status TEXT NOT NULL,
               fail_count INTEGER NOT NULL,
               first_fail_ts INTEGER,
               last_reason TEXT)"""
    )
    conn.commit()
    return conn


# ----------------------------------------------------------------------
# Probes
# ----------------------------------------------------------------------
def probe_http(t):
    """Returns (up, status_code, response_ms, error)."""
    start = time.time()
    try:
        ctx = None
        if not t.get("verify_ssl", True):
            ctx = ssl._create_unverified_context()
        req = Request(t["url"], headers={"User-Agent": "UptimeBot/1.0"})
        resp = urlopen(req, timeout=t.get("timeout", 10), context=ctx)
        code = resp.getcode()
        body = ""
        if t.get("must_contain"):
            body = resp.read(500000).decode("utf-8", "ignore")
        ms = int((time.time() - start) * 1000)
        if code > t.get("ok_status_max", 399):
            return False, code, ms, "HTTP %d" % code
        if t.get("must_contain") and t["must_contain"] not in body:
            return False, code, ms, "Expected text not found"
        return True, code, ms, None
    except HTTPError as e:
        ms = int((time.time() - start) * 1000)
        ok = e.code <= t.get("ok_status_max", 399)
        return ok, e.code, ms, (None if ok else "HTTP %d" % e.code)
    except (URLError, socket.timeout, ssl.SSLError, OSError) as e:
        ms = int((time.time() - start) * 1000)
        return False, None, ms, str(getattr(e, "reason", e))[:200]
    except Exception as e:  # noqa
        ms = int((time.time() - start) * 1000)
        return False, None, ms, "Error: %s" % str(e)[:200]


def probe_tcp(t):
    start = time.time()
    try:
        s = socket.create_connection((t["host"], int(t["port"])), timeout=t.get("timeout", 5))
        s.close()
        return True, None, int((time.time() - start) * 1000), None
    except Exception as e:
        return False, None, int((time.time() - start) * 1000), str(e)[:200]


def probe(t):
    fn = probe_tcp if t.get("type") == "tcp" else probe_http
    result = None
    for attempt in range(max(1, CONFIG["retries_per_check"])):
        result = fn(t)
        if result[0]:
            return result
        if attempt < CONFIG["retries_per_check"] - 1:
            time.sleep(2)
    return result


# ----------------------------------------------------------------------
# Alerts
# ----------------------------------------------------------------------
def send_email(subject, body):
    cfg = CONFIG["email"]
    if not cfg.get("enabled"):
        return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = cfg["from"]
        msg["To"] = ", ".join(cfg["to"])
        if int(cfg["smtp_port"]) == 465:
            server = smtplib.SMTP_SSL(cfg["smtp_host"], 465, timeout=20)
        else:
            server = smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=20)
            server.starttls()
        server.login(cfg["username"], cfg["password"])
        server.sendmail(cfg["from"], cfg["to"], msg.as_string())
        server.quit()
    except Exception as e:
        sys.stderr.write("Email failed: %s\n" % e)


def send_telegram(text):
    cfg = CONFIG["telegram"]
    if not cfg.get("enabled"):
        return
    try:
        url = "https://api.telegram.org/bot%s/sendMessage" % cfg["bot_token"]
        data = urlencode({"chat_id": cfg["chat_id"], "text": text}).encode("utf-8")
        urlopen(Request(url, data=data), timeout=15).read()
    except Exception as e:
        sys.stderr.write("Telegram failed: %s\n" % e)


def alert(subject, body):
    send_email(subject, body)
    send_telegram(subject + "\n" + body)


# ----------------------------------------------------------------------
# Core check logic
# ----------------------------------------------------------------------
def run_checks():
    conn = db_connect()
    ts = now_ts()

    for t in CONFIG["targets"]:
        name = t["name"]
        up, code, ms, err = probe(t)

        conn.execute(
            "INSERT INTO checks (target, ts, up, status_code, response_ms, error) VALUES (?,?,?,?,?,?)",
            (name, ts, 1 if up else 0, code, ms, err),
        )

        row = conn.execute("SELECT * FROM state WHERE target=?", (name,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO state (target, status, fail_count) VALUES (?, 'up', 0)", (name,)
            )
            status, fail_count, first_fail = "up", 0, None
        else:
            status, fail_count, first_fail = row["status"], row["fail_count"], row["first_fail_ts"]

        if up:
            if status == "down":
                inc = conn.execute(
                    "SELECT * FROM incidents WHERE target=? AND end_ts IS NULL "
                    "ORDER BY id DESC LIMIT 1", (name,)
                ).fetchone()
                duration = 0
                if inc:
                    conn.execute("UPDATE incidents SET end_ts=? WHERE id=?", (ts, inc["id"]))
                    duration = ts - inc["start_ts"]
                alert(
                    "[RECOVERED] %s is back UP" % name,
                    "%s is UP again.\nWent down: %s\nRecovered: %s\nDowntime: %s"
                    % (name, fmt_time(inc["start_ts"]) if inc else "?", fmt_time(ts),
                       fmt_duration(duration)),
                )
            conn.execute(
                "UPDATE state SET status='up', fail_count=0, first_fail_ts=NULL WHERE target=?",
                (name,),
            )
        else:
            fail_count += 1
            if first_fail is None:
                first_fail = ts
            if status == "up" and fail_count >= CONFIG["fail_threshold"]:
                status = "down"
                conn.execute(
                    "INSERT INTO incidents (target, start_ts, reason) VALUES (?,?,?)",
                    (name, first_fail, err),
                )
                alert(
                    "[DOWN] %s is DOWN" % name,
                    "%s appears to be DOWN.\nFirst failure: %s\nReason: %s"
                    % (name, fmt_time(first_fail), err),
                )
            conn.execute(
                "UPDATE state SET status=?, fail_count=?, first_fail_ts=?, last_reason=? "
                "WHERE target=?",
                (status, fail_count, first_fail, err, name),
            )

        print("[%s] %-20s %s %s" % (fmt_time(ts), name, "UP  " if up else "FAIL",
                                    ("(%d ms)" % ms) if up else (err or "")))

    # prune old raw checks (once in a while)
    if ts % 3600 < 60:
        cutoff = ts - CONFIG["keep_check_days"] * 86400
        conn.execute("DELETE FROM checks WHERE ts < ?", (cutoff,))

    conn.commit()
    conn.close()


# ----------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------
def target_report(conn, name, days, now):
    window_start = now - days * 86400
    first = conn.execute("SELECT MIN(ts) AS m FROM checks WHERE target=?", (name,)).fetchone()["m"]
    if first is None:
        return None
    window_start = max(window_start, first)  # don't count time before monitoring began
    window = max(1, now - window_start)

    incidents = conn.execute(
        "SELECT * FROM incidents WHERE target=? AND (end_ts IS NULL OR end_ts > ?) "
        "AND start_ts < ?", (name, window_start, now),
    ).fetchall()

    down = 0
    longest = 0
    for i in incidents:
        s = max(i["start_ts"], window_start)
        e = min(i["end_ts"] if i["end_ts"] else now, now)
        if e > s:
            down += e - s
            longest = max(longest, e - s)

    avg = conn.execute(
        "SELECT AVG(response_ms) AS a FROM checks WHERE target=? AND ts>=? AND up=1",
        (name, window_start),
    ).fetchone()["a"]

    return {
        "window": window, "down": down, "up": window - down,
        "pct": 100.0 * (window - down) / window,
        "incidents": len(incidents), "longest": longest, "avg_ms": avg,
    }


def build_report(days_list):
    conn = db_connect()
    now = now_ts()
    lines = ["UPTIME REPORT - generated %s" % fmt_time(now), "=" * 60]

    for t in CONFIG["targets"]:
        name = t["name"]
        st = conn.execute("SELECT status FROM state WHERE target=?", (name,)).fetchone()
        lines.append("")
        lines.append("%s   [current: %s]" % (name, (st["status"].upper() if st else "NO DATA")))
        lines.append("-" * 60)
        for d in days_list:
            r = target_report(conn, name, d, now)
            if r is None:
                lines.append("  No data yet.")
                break
            lines.append(
                "  Last %-3dd | uptime %7.3f%% | up %-12s | down %-12s | incidents %d | longest %s | avg %s"
                % (d, r["pct"], fmt_duration(r["up"]), fmt_duration(r["down"]),
                   r["incidents"], fmt_duration(r["longest"]),
                   ("%d ms" % r["avg_ms"]) if r["avg_ms"] else "n/a")
            )
    conn.close()
    return "\n".join(lines)


def list_incidents(limit=20):
    conn = db_connect()
    rows = conn.execute(
        "SELECT * FROM incidents ORDER BY start_ts DESC LIMIT ?", (limit,)
    ).fetchall()
    now = now_ts()
    print("%-20s %-20s %-20s %-12s %s" % ("Target", "Down since", "Recovered", "Duration", "Reason"))
    print("-" * 95)
    for r in rows:
        end = r["end_ts"]
        print("%-20s %-20s %-20s %-12s %s" % (
            r["target"][:20], fmt_time(r["start_ts"]),
            fmt_time(end) if end else "STILL DOWN",
            fmt_duration((end or now) - r["start_ts"]), r["reason"] or ""))
    conn.close()


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Server uptime/downtime bot")
    p.add_argument("command", choices=["check", "report", "incidents", "testalert"])
    p.add_argument("--days", type=int, help="report window in days")
    p.add_argument("--email", action="store_true", help="email the report too")
    args = p.parse_args()

    if args.command == "check":
        run_checks()
    elif args.command == "report":
        text = build_report([args.days] if args.days else [1, 7, 30])
        print(text)
        if args.email:
            send_email("Uptime report", text)
            send_telegram(text)
    elif args.command == "incidents":
        list_incidents()
    elif args.command == "testalert":
        alert("[TEST] Uptime bot", "If you can read this, alerts are working.")
        print("Test alert sent (check your inbox / Telegram).")


if __name__ == "__main__":
    main()

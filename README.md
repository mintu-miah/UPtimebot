# UPtimebot

A lightweight server **uptime and downtime monitor** written in pure Python. It is built to run on shared hosting with **cPanel cron jobs**, needs **no pip installs**, and keeps all its data in a local SQLite file.

It checks your website (or any TCP port) every minute, records every outage, alerts you by **email or Telegram** when something goes down and when it recovers, and reports exactly how much time your server was up and down.

---

## Features

- **HTTP and TCP checks**: monitor websites, APIs, MySQL, SSH, mail ports and more
- **Smart outage detection**: retries and a configurable failure threshold prevent false alarms
- **Accurate downtime tracking**: each incident stores its start time, recovery time, duration and reason
- **Uptime reports**: uptime % plus total up and down time for 24 hours, 7 days, 30 days or any custom window
- **Instant alerts**: email (any cPanel mailbox via SMTP) and Telegram, for both DOWN and RECOVERED events
- **Content check (optional)**: mark a site as down if an expected piece of text is missing from the page
- **Zero dependencies**: standard library only, works on Python 3.6+
- **Self-cleaning**: old raw check history is pruned automatically (incidents are kept forever)

---

## Quick start

```bash
git clone https://github.com/mintu-miah/UPtimebot.git
cd UPtimebot
nano uptime_bot.py          # edit the CONFIG block (URL, email, etc.)
python3 uptime_bot.py check
```

You should see something like:

```
[2026-10-08 12:00:00] Main website         UP   (230 ms)
```

Then schedule it with cron (see below).

---

## Configuration

All settings are in the `CONFIG` block at the top of `uptime_bot.py`.

| Setting | Description | Default |
|---|---|---|
| `targets` | List of things to monitor (HTTP or TCP) | one example site |
| `fail_threshold` | Consecutive failed checks before an outage is declared | `2` |
| `retries_per_check` | Retries inside a single check before it counts as failed | `2` |
| `keep_check_days` | How long raw check history is kept | `90` |
| `email` | SMTP settings for email alerts | disabled |
| `telegram` | Bot token and chat ID for Telegram alerts | disabled |

### Target examples

```python
"targets": [
    {   # Website check
        "name": "Main website",
        "type": "http",
        "url": "https://example.com",
        "timeout": 10,
        "ok_status_max": 399,
        "verify_ssl": True,
        # "must_contain": "Welcome",   # optional text that must be on the page
    },
    {   # TCP port check
        "name": "MySQL port",
        "type": "tcp",
        "host": "example.com",
        "port": 3306,
        "timeout": 5,
    },
]
```

---

## Commands

| Command | Description |
|---|---|
| `python3 uptime_bot.py check` | Run one check of all targets (this is what cron runs) |
| `python3 uptime_bot.py report` | Report for the last 24h, 7d and 30d |
| `python3 uptime_bot.py report --days 14` | Report for a custom number of days |
| `python3 uptime_bot.py report --email` | Print the report and send it by email/Telegram |
| `python3 uptime_bot.py incidents` | List recent outages with duration and reason |
| `python3 uptime_bot.py testalert` | Send a test alert to verify email/Telegram |

### Example report

```
UPTIME REPORT - generated 2026-10-08 08:00:00
============================================================

Main website   [current: UP]
------------------------------------------------------------
  Last 1  d | uptime 100.000% | up 1d         | down 0s    | incidents 0 | longest 0s  | avg 231 ms
  Last 7  d | uptime  99.821% | up 6d 22h 45m | down 1h 12m | incidents 3 | longest 45m | avg 240 ms
  Last 30 d | uptime  99.950% | up 29d 22h    | down 21m   | incidents 1 | longest 21m | avg 238 ms
```

---

## cPanel setup

1. Upload `uptime_bot.py` through **File Manager** into a folder **outside** `public_html` (for example `/home/YOURUSER/uptime_bot/`).
2. Edit the `CONFIG` block with your URL and email settings.
3. Open **Cron Jobs** and add:

```
* * * * * /usr/bin/python3 /home/YOURUSER/uptime_bot/uptime_bot.py check >> /home/YOURUSER/uptime_bot/bot.log 2>&1
```

4. Optional daily email report at 8:00 AM:

```
0 8 * * * /usr/bin/python3 /home/YOURUSER/uptime_bot/uptime_bot.py report --email >> /home/YOURUSER/uptime_bot/bot.log 2>&1
```

Replace `YOURUSER` with your cPanel username. Run `which python3` in Terminal if your Python lives somewhere else.

The full step-by-step guide, including Telegram setup and troubleshooting, is in **[MANUAL_cPanel.md](MANUAL_cPanel.md)**.

---

## Important: where to run the bot

If the bot runs on the **same server** it monitors, it cannot run while that server is completely down. It will still catch Apache, PHP and MySQL failures, but it will miss a full outage.

For accurate numbers, run UPtimebot from a **different host**: a second cPanel account, a small VPS, a home PC or a Raspberry Pi. The setup is identical. It only needs internet access to reach your server.

---

## How it works

1. Each run probes every target, retrying up to `retries_per_check` times.
2. Every result is saved to SQLite (`uptime.db`).
3. After `fail_threshold` failures in a row, an **incident** opens, starting from the first failed check, and a DOWN alert is sent.
4. On the next successful check the incident closes, a RECOVERED alert is sent with the total downtime, and the stats update.
5. Uptime % is calculated as `(window - downtime) / window`. Time before monitoring began is never counted.

---

## Project structure

```
UPtimebot/
├── uptime_bot.py       # the bot (config, checks, alerts, reports)
├── MANUAL_cPanel.md    # detailed cPanel guide
├── README.md
└── uptime.db           # created automatically on first run (do not commit)
```

Add this to your `.gitignore` so your data and logs stay private:

```
uptime.db
bot.log
```

---

## Security tips

- Keep the folder outside `public_html` so the database and config cannot be downloaded.
- `chmod 600 uptime_bot.py`, because it contains your SMTP password.
- Use a dedicated mailbox for alerts.
- Never commit real passwords or Telegram tokens to GitHub. Replace them with placeholders before pushing.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Nothing appears in `bot.log` | Check the Python path in the cron command and run the same command in Terminal |
| `CERTIFICATE_VERIFY_FAILED` | Fix the site's SSL certificate, or set `"verify_ssl": False` for that target |
| Email not arriving | Use port 465 (SSL), verify the credentials, and look for `Email failed` in `bot.log` |
| False DOWN alerts | Raise `fail_threshold` to 3 and `timeout` to 15 |
| Host blocks one-minute cron | Use `*/5 * * * *` and set `fail_threshold` to 1 or 2 |

---

## Roadmap ideas

- Web dashboard with uptime charts
- Multiple alert channels (Slack, Discord, WhatsApp)
- SSL certificate expiry warnings
- Maintenance windows (planned downtime excluded from stats)

---

## Contributing

Issues and pull requests are welcome. Fork the repo, create a feature branch, and open a PR describing your change.

## License

MIT. Add a `LICENSE` file to the repo to make this official.

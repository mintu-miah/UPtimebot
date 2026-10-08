# Uptime Bot - cPanel Manual

## 0. Read this first: where to run the bot

If the bot runs **on the same server it monitors**, it cannot run while that server is fully down, so it will miss the outage itself (it only sees the gap afterwards). It will still catch problems like Apache, PHP or MySQL failing while cPanel cron keeps running.

For true server up/down tracking, run the bot from a **different** host:
- a second cPanel hosting account (even a cheap one), or
- a small VPS / home PC / Raspberry Pi with `python3` and `cron`.

The setup below is identical either way. The bot only needs to reach your server over the internet.

---

## 1. Upload the script

1. cPanel -> **File Manager**.
2. Go **outside** `public_html` (for example `/home/YOURUSER/`), so nobody can download your config or database.
3. Create a folder: `uptime_bot`.
4. Open it -> **Upload** -> upload `uptime_bot.py`.

## 2. Edit the configuration

Right-click `uptime_bot.py` -> **Edit**. Change the `CONFIG` block near the top:

| Setting | What to put |
|---|---|
| `targets -> url` | Your website URL, e.g. `https://yourdomain.com` |
| `targets -> name` | A label, e.g. `Main website` |
| `fail_threshold` | `2` = confirm failure on 2 checks in a row (avoids false alarms) |
| `email.enabled` | `True` to get email alerts |
| `email.smtp_host / username / password / to` | Use a mailbox from cPanel -> **Email Accounts** -> *Connect Devices* (shows the SMTP host and port) |

You can add more targets (other sites, or a TCP port such as MySQL 3306 or SSH 22). An example is in the file.

## 3. Test it (Terminal)

cPanel -> **Terminal** (or SSH):

```bash
cd ~/uptime_bot
which python3
python3 --version      # needs 3.6 or newer
python3 uptime_bot.py check
```

You should see a line like `[2026-10-08 12:00:00] Main website   UP   (230 ms)`.

If you have no Terminal: use **Cron Jobs** (step 4) with a one-minute schedule, then look at the log file.

Test alerts:

```bash
python3 uptime_bot.py testalert
```

## 4. Schedule it with Cron Jobs

cPanel -> **Cron Jobs** -> *Add New Cron Job*.

**Check every minute** (Common Settings: *Once Per Minute*, or set all fields to `*`):

```
* * * * * /usr/bin/python3 /home/YOURUSER/uptime_bot/uptime_bot.py check >> /home/YOURUSER/uptime_bot/bot.log 2>&1
```

**Daily report by email at 8:00 AM** (optional):

```
0 8 * * * /usr/bin/python3 /home/YOURUSER/uptime_bot/uptime_bot.py report --email >> /home/YOURUSER/uptime_bot/bot.log 2>&1
```

Replace `YOURUSER` with your cPanel username. If `which python3` printed a different path (e.g. `/opt/alt/python39/bin/python3`), use that instead of `/usr/bin/python3`.

Some hosts do not allow a one-minute interval. Then use every 5 minutes (`*/5 * * * *`), and set `fail_threshold` to `1` or `2`.

## 5. Using the bot

Run in Terminal:

| Command | What it does |
|---|---|
| `python3 uptime_bot.py report` | Uptime % and downtime for last 24h, 7d and 30d |
| `python3 uptime_bot.py report --days 14` | Custom window |
| `python3 uptime_bot.py report --email` | Prints and emails the report |
| `python3 uptime_bot.py incidents` | List of every outage: start, recovery, duration, reason |
| `python3 uptime_bot.py testalert` | Sends a test email/Telegram |

Example report line:

```
Last 7  d | uptime  99.821% | up 6d 22h 45m | down 1h 12m | incidents 3 | longest 45m | avg 240 ms
```

Uptime % only counts time since the bot started monitoring, so a new install is not penalised.

## 6. Optional: Telegram alerts (free, instant on phone)

1. In Telegram, message **@BotFather** -> `/newbot` -> copy the **token**.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/botYOUR_TOKEN/getUpdates` in a browser and copy the number after `"chat":{"id":`.
4. In `CONFIG["telegram"]` set `enabled: True`, `bot_token`, `chat_id`.
5. Run `python3 uptime_bot.py testalert`.

## 7. How outages are decided

1. Each check tries the target up to `retries_per_check` times.
2. After `fail_threshold` failed checks in a row, an incident is opened. Its start time is the **first** failed check.
3. On the next successful check the incident is closed, a RECOVERED alert is sent, and the downtime is recorded.
4. Uptime % = (time in window - total incident time) / time in window.

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| Nothing in `bot.log` | Check the cron path to `python3` and the script. Run the same command in Terminal. |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Set `"verify_ssl": False` for that target (or fix the certificate). |
| Email not arriving | Port 465 needs SSL. Check username/password. Look at `bot.log` for `Email failed`. |
| False "down" alerts | Raise `fail_threshold` to 3 and `timeout` to 15. |
| `uptime.db` permission error | Keep the folder writable, `chmod 755 ~/uptime_bot`. |
| Want to reset all data | Delete `uptime.db` (it is recreated automatically). |

## 9. Security tips

- Keep the folder **outside** `public_html`.
- `chmod 600 uptime_bot.py` since it holds your email password.
- Use a dedicated mailbox for alerts, not your main one.

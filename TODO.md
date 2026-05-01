# TODO

## Alerts

Goal: notify the admin when an app is unhealthy, restarted, or repeatedly failing.

Planned simple options:

- Telegram bot alert
- Email alert via SMTP
- Webhook alert for custom systems

Basic config idea:

```yaml
alerts:
  enabled: true
  telegram:
    bot_token: ""
    chat_id: ""
  email:
    smtp_host: ""
    smtp_port: 587
    username: ""
    password: ""
    to: "admin@example.com"
```

Possible commands:

```bash
fmanager alert test
fmanager alert status
```

Doctor integration:

```bash
fmanager doctor --fix --alert
```

Alert triggers:

- service is inactive
- app was restarted by doctor
- port is not listening
- app failed repeatedly within a short time
- nginx config is missing

Keep it simple: alerts should only send one message per issue within a cooldown window to avoid spam.

---

## Error scanner

Goal: scan recent app logs for common crash/error patterns.

Possible command:

```bash
fmanager scan-errors app1
fmanager scan-errors app1 --lines 300
fmanager scan-errors --all
```

Patterns to detect:

- Traceback
- Exception
- Error
- CRITICAL
- ModuleNotFoundError
- ImportError
- Permission denied
- Address already in use
- database connection errors
- gunicorn worker timeout

Example output:

```text
APP   LEVEL   MATCHES   LAST ERROR
app1  HIGH    3         ModuleNotFoundError: No module named 'requests'
api   MEDIUM  1         gunicorn worker timeout
```

Implementation idea:

- read from `journalctl -u SERVICE -n LINES --no-pager`
- search with simple regex patterns
- return grouped results
- optional `--alert` to notify if serious errors are found

Keep it lightweight: no external log database, no Elasticsearch, no heavy stack.

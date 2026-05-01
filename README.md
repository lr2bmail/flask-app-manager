# Flask App Manager

Lightweight CLI to manage multiple Flask apps on one server.

## Features

- Manage apps (start/stop/restart)
- Monitor (status, CPU, RAM)
- Auto restart (self-healing)
- Deploy (git + pip + restart)
- Backup
- Create apps automatically
- SSL (Certbot)
- Cloudflare support
- Simple web dashboard

---

## Install

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv nginx -y

git clone https://github.com/lr2bmail/flask-app-manager.git
cd flask-app-manager
bash install.sh
```

---

## Quick start

Create app:

```bash
sudo fmanager create app1 --domain app1.com
```

Start it:

```bash
sudo fmanager gen-systemd app1
sudo systemctl daemon-reload
sudo systemctl enable --now app1

sudo fmanager gen-nginx app1
sudo systemctl reload nginx
```

---

## Useful commands

```bash
fmanager list
fmanager status
fmanager top
fmanager logs app1
fmanager deploy app1
fmanager backup app1
fmanager doctor
```

---

## Auto restart

```bash
sudo fmanager autorestart
```

---

## Web dashboard

```bash
fmanager web --port 5050 --password 1234
```

---

## Notes

- Designed to be simple (no Docker, no panels)
- Works with systemd + nginx + gunicorn
- Extendable but minimal by default

# Flask App Manager

![Tests](https://github.com/lr2bmail/flask-app-manager/actions/workflows/tests.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Ubuntu](https://img.shields.io/badge/Ubuntu-20.04%2B-orange)
![Nginx](https://img.shields.io/badge/Nginx-supported-brightgreen)
![systemd](https://img.shields.io/badge/systemd-supported-lightgrey)
![Status](https://img.shields.io/badge/status-active-success)
![License](https://img.shields.io/badge/license-MIT-green)

Lightweight CLI to manage multiple Flask apps on one Ubuntu server.

## Features

- Manage apps: start / stop / restart
- Monitor apps: status, ports, CPU, RAM
- Auto restart / self-healing with systemd timer
- Deploy apps: `git pull` + `pip install` + restart
- Backup app files
- Create simple Flask apps automatically
- Generate systemd services
- Generate Nginx configs
- SSL with Certbot
- Cloudflare helper support
- Simple read-only web dashboard

---

## One-command install

Run this on a fresh Ubuntu server:

```bash
curl -fsSL https://raw.githubusercontent.com/lr2bmail/flask-app-manager/main/install.sh | bash
```

Then test:

```bash
fmanager list
```

Safer install method:

```bash
curl -fsSL https://raw.githubusercontent.com/lr2bmail/flask-app-manager/main/install.sh -o install.sh
cat install.sh
bash install.sh
```

---

## Manual install

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv nginx git -y

git clone https://github.com/lr2bmail/flask-app-manager.git
cd flask-app-manager
bash install.sh
```

---

## Quick start

Create your first Flask app:

```bash
sudo fmanager create app1 --domain app1.com
```

Create systemd service:

```bash
sudo fmanager gen-systemd app1
sudo systemctl daemon-reload
sudo systemctl enable --now app1
```

Create Nginx config:

```bash
sudo fmanager gen-nginx app1
sudo nginx -t
sudo systemctl reload nginx
```

Add SSL:

```bash
sudo fmanager ssl app1 --email you@example.com --redirect
```

---

## Existing Flask app

If you already have an app:

```bash
sudo fmanager add app1 \
  --path /apps/app1 \
  --domain app1.com \
  --port 8001 \
  --module app:app
```

Then generate systemd + Nginx:

```bash
sudo fmanager gen-systemd app1
sudo systemctl daemon-reload
sudo systemctl enable --now app1

sudo fmanager gen-nginx app1
sudo nginx -t
sudo systemctl reload nginx
```

---

## Useful commands

```bash
fmanager list
fmanager status
fmanager status app1
fmanager top
fmanager ports
fmanager logs app1
fmanager logs app1 --lines 100
fmanager logs app1 -f
fmanager info app1
fmanager check app1
fmanager doctor
```

---

## Deploy

```bash
sudo fmanager deploy app1
```

---

## Backup

```bash
sudo fmanager backup app1
```

---

## Auto restart / self-healing

```bash
sudo fmanager autorestart
```

---

## Web dashboard

```bash
fmanager web --port 5050 --password strong-password
```

---

## Notes

- Designed to be simple: no Docker, no Kubernetes, no hosting panel
- Works with systemd + Nginx + Gunicorn

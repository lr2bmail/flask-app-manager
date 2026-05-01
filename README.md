# Flask App Manager

A simple CLI tool to manage multiple Flask apps on one Ubuntu server.

It can:

- List apps
- Check systemd status
- Start / stop / restart apps
- Show recent logs
- Add app config
- Generate systemd service files
- Generate Nginx config files
- Check ports
- Show basic app information

Recommended stack:

```text
Nginx → Gunicorn → Flask app
systemd keeps each app alive
```

## Install

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv nginx -y

git clone https://github.com/lr2bmail/flask-app-manager.git
cd flask-app-manager
bash install.sh
```

## Add app

```bash
sudo fmanager add app1 \
  --path /apps/app1 \
  --domain app1.example.com \
  --port 8001 \
  --module app:app
```

## Generate systemd service

```bash
sudo fmanager gen-systemd app1
sudo systemctl daemon-reload
sudo systemctl enable app1
sudo systemctl start app1
```

## Generate Nginx config

```bash
sudo fmanager gen-nginx app1
sudo nginx -t
sudo systemctl reload nginx
```

## Commands

```bash
fmanager list
fmanager status
fmanager status app1
fmanager start app1
fmanager stop app1
fmanager restart app1
fmanager logs app1
fmanager logs app1 --lines 100
fmanager logs app1 -f
fmanager ports
fmanager info app1
fmanager check app1
```

## SSL with Certbot

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d app1.example.com
```

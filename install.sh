#!/usr/bin/env bash
set -e

sudo apt update
sudo apt install python3 python3-pip python3-venv nginx -y

pip3 install -r requirements.txt

sudo cp fmanager.py /usr/local/bin/fmanager
sudo chmod +x /usr/local/bin/fmanager

sudo mkdir -p /etc/fmanager
if [ ! -f /etc/fmanager/apps.yml ]; then
  sudo cp apps.example.yml /etc/fmanager/apps.yml
fi

echo "Done. Try: fmanager list"

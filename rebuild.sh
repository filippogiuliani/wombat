#!/bin/sh
# Rebuild and restart Wombat. Data stays in ./Wombat_data (mounted on /app/data).
set -e
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  echo "The .env file is missing: copy .env.example to .env and configure it." >&2
  exit 1
fi
sudo docker build -t wombat .
sudo docker rm -f wombat 2>/dev/null || true
sudo docker run -d --restart unless-stopped -p 3004:3004 --env-file .env \
  -v "$(pwd)/Wombat_data:/app/data" --name wombat wombat
echo "Wombat started on http://localhost:3004"

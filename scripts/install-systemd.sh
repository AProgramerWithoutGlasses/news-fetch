#!/bin/sh
set -eu
cd /opt/news-fetch
if ! id news-fetch >/dev/null 2>&1; then
  useradd --system --home-dir /opt/news-fetch --shell /usr/sbin/nologin news-fetch
fi
mkdir -p data reports
chown news-fetch:news-fetch data reports
if [ ! -f .env ]; then cp .env.example .env; fi
chown root:news-fetch .env
chmod 640 .env
install -m 644 scripts/news-fetch.service scripts/news-fetch.timer scripts/news-fetch-web.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now news-fetch.timer news-fetch-web.service
systemctl start --no-block news-fetch.service

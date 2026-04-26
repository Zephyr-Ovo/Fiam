# Deployment notes — fiet.cc dashboard

## Topology

```
         Internet
            │ :443
            ▼
     ┌──────────────────┐
     │   Cloudflare     │  public TLS + tunnel
     └────────┬─────────┘
              │ cloudflared → localhost:80
              ▼
     ┌──────────────────┐
     │  Caddy (HTTP)    │  basic auth: iris / ai / fiet
     │  /api/* ─────┐   │  static dashboard
     └──────────────┼───┘
                    │ localhost:8766
                    ▼
     ┌──────────────────┐        reads
     │  dashboard_      │◄────── store/, logs/, home/
     │  server.py       │
     └──────────────────┘
```

## One-time setup on ISP

```bash
# 1. Harden server (interactive)
sudo bash scripts/harden_server.sh

# 2. Point Cloudflare Tunnel ingress at Caddy
#    /home/fiet/.cloudflared/config.yml:
#      ingress:
#        - hostname: fiet.cc
#          service: http://localhost:80
#        - hostname: www.fiet.cc
#          service: http://localhost:80
#        - service: http_status:404
#    sudo systemctl restart cloudflared

# 3. Node (if not already installed)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs

# 4. Build the dashboard
cd ~/fiam-code/dashboard
npm install
npm run build   # output in ./build

# 5. Make basic-auth hashes — paste each into Caddyfile
caddy hash-password

# 6. Install the Caddyfile
sudo cp ~/fiam-code/deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile   # fill in 3 password hashes
sudo setfacl -m u:caddy:--x /home/fiet   # allow Caddy to traverse to dashboard/build
sudo systemctl reload caddy

# 7. Start the dashboard backend under systemd
grep -q '^FIAM_INGEST_TOKEN=' ~/fiam-code/.env || printf '\nFIAM_INGEST_TOKEN=%s\n' "$(openssl rand -hex 32)" >> ~/fiam-code/.env
sudo cp ~/fiam-code/deploy/fiam-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fiam-dashboard
```

## MQTT bus (Mosquitto) — replaces channel polling

The daemon, dashboard, and channel bridges talk over a local Mosquitto
broker bound to `127.0.0.1:1883`. No external exposure, no auth.

```bash
# Install Mosquitto
sudo apt-get install -y mosquitto mosquitto-clients

# Drop in the fiam config (loopback + persistence)
sudo cp ~/fiam-code/deploy/mosquitto-fiam.conf /etc/mosquitto/conf.d/fiam.conf
sudo systemctl restart mosquitto

# Verify the bus is up
mosquitto_sub -h 127.0.0.1 -t 'fiam/#' -v &
mosquitto_pub -h 127.0.0.1 -t 'fiam/receive/test' -m '{"text":"hi","source":"test"}'

# Install the channel bridges
sudo cp ~/fiam-code/deploy/fiam-bridge-tg.service /etc/systemd/system/
sudo cp ~/fiam-code/deploy/fiam-bridge-email.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fiam-bridge-tg fiam-bridge-email

# Start the daemon — it subscribes to fiam/receive/+ instead of channel polling
sudo cp ~/fiam-code/deploy/fiam-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fiam-daemon
```

### Topic layout

| Topic                       | Direction | Payload (JSON)                                    |
|-----------------------------|-----------|----------------------------------------------------|
| `fiam/receive/<source>`     | inbound   | `{text, source, from_name, t, ...meta}`           |
| `fiam/dispatch/<target>`    | outbound  | `{text, recipient}`                               |

Sources currently in use: `tg`, `email`, `favilla`.
Targets currently in use: `tg`, `email`.

## Update deployment

```bash
cd ~/fiam-code
git pull
cd dashboard && npm install && npm run build
sudo systemctl restart fiam-dashboard   # only if backend changed
```

## Access

- https://fiet.cc — Cloudflare TLS, Caddy basic auth
- `/api/capture`, `/api/app/*`, and `/api/wearable/*` bypass Caddy basic auth and are protected by `FIAM_INGEST_TOKEN`
- backend decides role from `X-Forwarded-User` (set by Caddy from auth user id)
- roles: `iris` / `ai` / `fiet` (everyone else → `anon`, blocked by Caddy)

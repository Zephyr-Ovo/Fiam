# Deployment notes — fiet.cc dashboard

## Topology

```
         Internet
            │ :443
            ▼
     ┌──────────────────┐
     │  Caddy (HTTPS)   │  auto Let's Encrypt
     │  /api/* ─────┐   │  basic auth: iris / ai / fiet
     │  /*  static  │   │
     └──────────────┼───┘
                    │ :8766 (localhost)
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

# 2. Point DNS
#    A      fiet.cc          <ISP public IP>
#    A      www.fiet.cc      <ISP public IP>
#    (optional) A  *.fiet.cc <ISP public IP>

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
sudo nano /etc/caddy/Caddyfile   # fill in email, 3 password hashes
sudo systemctl reload caddy

# 7. Start the dashboard backend under systemd
sudo cp ~/fiam-code/deploy/fiam-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fiam-dashboard
```

## Update deployment

```bash
cd ~/fiam-code
git pull
cd dashboard && npm install && npm run build
sudo systemctl restart fiam-dashboard   # only if backend changed
```

## Access

- https://fiet.cc — prompts for basic auth
- backend decides role from `X-Forwarded-User` (set by Caddy from auth user id)
- roles: `iris` / `ai` / `fiet` (everyone else → `anon`, blocked by Caddy)

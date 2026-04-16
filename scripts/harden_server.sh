#!/usr/bin/env bash
# harden_server.sh — interactive server hardening for the ISP host.
#
# Run on the SERVER (not from your laptop). Each step asks for confirmation.
# Re-runnable: skips already-applied steps where possible.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Zephyr-Ovo/Fiam/main/scripts/harden_server.sh -o harden.sh
#   chmod +x harden.sh && sudo ./harden.sh
#
# Or after `git pull`:
#   sudo bash scripts/harden_server.sh

set -u

if [[ $EUID -ne 0 ]]; then
    echo "[!] must run as root (sudo)"
    exit 1
fi

# Colors
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; B='\033[0;36m'; N='\033[0m'

confirm() {
    # confirm "prompt" — returns 0 if yes
    local prompt="$1"
    read -r -p "$(echo -e "${Y}?${N} ${prompt} [y/N] ")" ans
    [[ "$ans" =~ ^[Yy]$ ]]
}

step() {
    echo
    echo -e "${B}===${N} $1"
}

ok()   { echo -e "${G}\xe2\x9c\x93${N} $1"; }
skip() { echo -e "${Y}-${N} skipped: $1"; }
warn() { echo -e "${R}!${N} $1"; }

# Detect package manager
if command -v apt-get >/dev/null; then
    PKG="apt-get"
elif command -v dnf >/dev/null; then
    PKG="dnf"
else
    warn "no apt/dnf — script targets Debian/Ubuntu/Fedora only"
    exit 1
fi

echo -e "${B}fiam server hardening${N}"
echo "Current host: $(hostname)  ($(hostname -I | awk '{print $1}'))"
echo "Distro: $(. /etc/os-release && echo "$PRETTY_NAME")"
echo
echo "This will walk through:"
echo "  1. UFW firewall (default deny inbound)"
echo "  2. fail2ban (auto-ban brute force)"
echo "  3. Unattended security upgrades"
echo "  4. SSH hardening (no root, no password)"
echo "  5. Optional: change SSH port"
echo "  6. Optional: install Caddy (HTTPS reverse proxy)"
echo

# ---------------------------------------------------------------------------
step "1/6  UFW firewall"
if command -v ufw >/dev/null; then
    ok "ufw already installed"
else
    if confirm "Install ufw?"; then
        $PKG install -y ufw && ok "installed"
    else
        skip "ufw not installed"
    fi
fi

if command -v ufw >/dev/null; then
    if confirm "Set default policy: deny incoming, allow outgoing, allow SSH(22), HTTP(80), HTTPS(443)?"; then
        ufw --force reset >/dev/null
        ufw default deny incoming
        ufw default allow outgoing
        ufw allow 22/tcp comment "ssh"
        ufw allow 80/tcp comment "http"
        ufw allow 443/tcp comment "https"
        ufw --force enable
        ufw status verbose
        ok "ufw active"
    else
        skip "ufw policy unchanged"
    fi
fi

# ---------------------------------------------------------------------------
step "2/6  fail2ban"
if command -v fail2ban-client >/dev/null; then
    ok "fail2ban already installed"
else
    if confirm "Install fail2ban?"; then
        $PKG install -y fail2ban && ok "installed"
    fi
fi

if command -v fail2ban-client >/dev/null; then
    JAIL=/etc/fail2ban/jail.local
    if [[ ! -f "$JAIL" ]]; then
        if confirm "Write default jail.local (sshd enabled, 1h ban, 5 retries)?"; then
            cat > "$JAIL" <<'EOF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5
backend = systemd

[sshd]
enabled = true
EOF
            systemctl enable --now fail2ban
            systemctl restart fail2ban
            ok "fail2ban configured"
        fi
    else
        skip "jail.local exists"
    fi
fi

# ---------------------------------------------------------------------------
step "3/6  Unattended security upgrades"
if [[ "$PKG" == "apt-get" ]]; then
    if dpkg -l | grep -q unattended-upgrades; then
        ok "unattended-upgrades already installed"
    elif confirm "Install unattended-upgrades?"; then
        $PKG install -y unattended-upgrades apt-listchanges
        dpkg-reconfigure -plow unattended-upgrades
        ok "configured"
    fi
else
    if confirm "Enable dnf-automatic for security updates?"; then
        dnf install -y dnf-automatic
        sed -i 's/^apply_updates.*/apply_updates = yes/' /etc/dnf/automatic.conf
        sed -i 's/^upgrade_type.*/upgrade_type = security/' /etc/dnf/automatic.conf
        systemctl enable --now dnf-automatic.timer
        ok "configured"
    fi
fi

# ---------------------------------------------------------------------------
step "4/6  SSH hardening"
SSHD=/etc/ssh/sshd_config
backup="${SSHD}.bak.$(date +%s)"
if confirm "Disable root login + password auth in $SSHD? (backup: $backup)"; then
    cp "$SSHD" "$backup"
    sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' "$SSHD"
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' "$SSHD"
    sed -i 's/^#\?ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' "$SSHD"
    sed -i 's/^#\?KbdInteractiveAuthentication.*/KbdInteractiveAuthentication no/' "$SSHD"
    if sshd -t; then
        systemctl reload sshd 2>/dev/null || systemctl reload ssh
        ok "sshd reloaded"
    else
        warn "sshd config invalid — restoring backup"
        cp "$backup" "$SSHD"
    fi
fi

# ---------------------------------------------------------------------------
step "5/6  Change SSH port (optional)"
if confirm "Change SSH port from 22 to a custom port? (reduces scan noise)"; then
    read -r -p "  new port (e.g. 22022): " newport
    if [[ "$newport" =~ ^[0-9]+$ ]] && (( newport > 1024 && newport < 65536 )); then
        sed -i "s/^#\?Port .*/Port $newport/" "$SSHD"
        if command -v ufw >/dev/null; then
            ufw allow "${newport}/tcp" comment "ssh-custom"
        fi
        if sshd -t; then
            warn "SSH will move to port $newport AFTER you reconnect."
            warn "Open a NEW terminal NOW: ssh -p $newport user@host"
            warn "Only after the new port works, run: sudo ufw delete allow 22/tcp"
            if confirm "Reload sshd now?"; then
                systemctl reload sshd 2>/dev/null || systemctl reload ssh
                ok "sshd reloaded — new port active"
            fi
        else
            warn "sshd config invalid — reverting"
            sed -i "s/^Port $newport/#Port 22/" "$SSHD"
        fi
    else
        warn "invalid port"
    fi
fi

# ---------------------------------------------------------------------------
step "6/6  Caddy (HTTPS reverse proxy, optional)"
if command -v caddy >/dev/null; then
    ok "caddy already installed"
elif confirm "Install Caddy? (for fiet.cc dashboard with auto HTTPS)"; then
    if [[ "$PKG" == "apt-get" ]]; then
        $PKG install -y debian-keyring debian-archive-keyring apt-transport-https curl
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
            | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
            > /etc/apt/sources.list.d/caddy-stable.list
        $PKG update && $PKG install -y caddy
        ok "caddy installed — config at /etc/caddy/Caddyfile"
    else
        $PKG install -y 'dnf-command(copr)'
        dnf copr enable -y @caddy/caddy
        dnf install -y caddy
    fi
fi

echo
echo -e "${G}done${N}"
echo
echo "Verify:"
echo "  ufw status verbose"
echo "  fail2ban-client status sshd"
echo "  systemctl status unattended-upgrades  # or dnf-automatic.timer"
echo
echo "Recommended next:"
echo "  - WHOIS privacy + registrar lock on your domain"
echo "  - Check no dangling DNS records (subdomain takeover)"
echo "  - Add SPF/DKIM/DMARC if sending email from this domain"

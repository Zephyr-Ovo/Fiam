#!/bin/bash
# ISP 服务器身份与安全检查脚本

ERRORS=0
WARNINGS=0
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}$*${NC}"; }
warn() { echo -e "${YELLOW}$*${NC}"; WARNINGS=$((WARNINGS+1)); }
err()  { echo -e "${RED}$*${NC}";   ERRORS=$((ERRORS+1)); }

echo ""
echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}  ISP 服务器检查  $(date '+%Y-%m-%d %H:%M:%S')${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""

# ── [1] 网络出口 ──────────────────────────────────────────
echo -e "${CYAN}--- [1] 网络出口 ---${NC}"
IP_INFO=$(curl -s --max-time 10 ipinfo.io 2>/dev/null)
if [ -z "$IP_INFO" ]; then
    err "无法获取出口IP ❌"
else
    IP=$(echo "$IP_INFO" | grep '"ip"' | cut -d'"' -f4)
    CITY=$(echo "$IP_INFO" | grep '"city"' | cut -d'"' -f4)
    REGION=$(echo "$IP_INFO" | grep '"region"' | cut -d'"' -f4)
    COUNTRY=$(echo "$IP_INFO" | grep '"country"' | cut -d'"' -f4)
    ORG=$(echo "$IP_INFO" | grep '"org"' | cut -d'"' -f4)
    echo "出口IP  : $IP"
    echo "位置    : $CITY, $REGION, $COUNTRY"
    echo "ORG     : $ORG"
    if echo "$ORG" | grep -qiE "Amazon|Google Cloud|Azure|DigitalOcean|Vultr|Alibaba|Tencent|Linode|OVH|Hetzner|Cloudflare"; then
        err "IP类型  : 数据中心 ❌ (期望住宅/ISP)"
    else
        ok "IP类型  : 住宅/ISP ✅"
    fi
    if [ "$COUNTRY" = "US" ]; then
        ok "国家    : US ✅"
    else
        err "国家    : $COUNTRY ❌ 期望 US"
    fi
fi
echo ""

# ── [2] 代理痕迹 ─────────────────────────────────────────
echo -e "${CYAN}--- [2] 代理痕迹 ---${NC}"
PROXY_DIRTY=0
for VAR in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY ANTHROPIC_BASE_URL; do
    VAL=$(printenv "$VAR" 2>/dev/null)
    if [ -n "$VAL" ]; then
        err "  $VAR = $VAL ← CC 遥测会上报 ❌"
        PROXY_DIRTY=1
    fi
done
[ $PROXY_DIRTY -eq 0 ] && ok "代理变量: 无 ✅"
echo ""

# ── [3] SSH 安全配置 ──────────────────────────────────────
echo -e "${CYAN}--- [3] SSH 安全 ---${NC}"
PASS_AUTH=$(sshd -T 2>/dev/null | grep "^passwordauthentication" | awk '{print $2}')
if [ "$PASS_AUTH" = "yes" ]; then
    warn "PasswordAuthentication: yes ⚠️  建议改为 no（已有密钥后）"
else
    ok "PasswordAuthentication: $PASS_AUTH ✅"
fi
ROOT_LOGIN=$(sshd -T 2>/dev/null | grep "^permitrootlogin" | awk '{print $2}')
echo "PermitRootLogin: $ROOT_LOGIN"
KEY_COUNT=$(wc -l < /root/.ssh/authorized_keys 2>/dev/null || echo 0)
ok "authorized_keys: $KEY_COUNT 条"
echo ""

# ── [4] Claude Code 状态 ─────────────────────────────────
echo -e "${CYAN}--- [4] Claude Code ---${NC}"
if command -v claude &>/dev/null; then
    CC_VER=$(claude --version 2>/dev/null)
    CC_PATH=$(which claude)
    ok "版本    : $CC_VER ✅"
    echo "路径    : $CC_PATH"
    if [ -f "/root/.claude/settings.json" ]; then
        DID=$(python3 -c "import json; d=json.load(open('/root/.claude/settings.json')); print(d.get('deviceId','N/A'))" 2>/dev/null)
        if [ -n "$DID" ] && [ "$DID" != "N/A" ]; then
            echo "DeviceID: ${DID:0:8}...${DID: -4}"
        fi
        ok "settings.json : 存在 ✅"
    else
        warn "settings.json 不存在（CC 未初始化）⚠️"
    fi
else
    warn "Claude Code: 未安装 ⚠️"
fi
echo ""

# ── [5] 运行时 ────────────────────────────────────────────
echo -e "${CYAN}--- [5] 运行时 ---${NC}"
NODE_VER=$(node --version 2>/dev/null)
[ -n "$NODE_VER" ] && ok "Node.js : $NODE_VER ✅" || warn "Node.js : 未安装 ⚠️"
PY_VER=$(python3 --version 2>/dev/null)
[ -n "$PY_VER" ] && ok "Python  : $PY_VER ✅" || warn "Python3 : 未安装 ⚠️"
UV_VER=$(uv --version 2>/dev/null)
[ -n "$UV_VER" ] && ok "uv      : $UV_VER ✅" || warn "uv      : 未安装（Fiam 部署需要）⚠️"
echo ""

# ── [6] Fiam 状态 ─────────────────────────────────────────
echo -e "${CYAN}--- [6] Fiam ---${NC}"
if [ -d "/root/Fiam" ]; then
    ok "仓库    : /root/Fiam ✅"
    if [ -f "/root/Fiam/fiam.toml" ]; then
        ok "fiam.toml : 存在 ✅"
    else
        warn "fiam.toml : 不存在（cp fiam.toml.example fiam.toml）⚠️"
    fi
    if [ -d "/root/Fiam/.venv" ]; then
        ok "venv    : 存在 ✅"
    else
        warn "venv    : 未创建 ⚠️"
    fi
else
    warn "Fiam 未克隆 (/root/Fiam 不存在) ⚠️"
fi
echo ""

# ── [7] 资源 ──────────────────────────────────────────────
echo -e "${CYAN}--- [7] 资源 ---${NC}"
MEM_TOTAL=$(free -m | awk '/^Mem:/{print $2}')
MEM_USED=$(free -m | awk '/^Mem:/{print $3}')
MEM_FREE=$(free -m | awk '/^Mem:/{print $7}')
SWAP_TOTAL=$(free -m | awk '/^Swap:/{print $2}')
echo "内存    : ${MEM_USED}MB used / ${MEM_TOTAL}MB total (${MEM_FREE}MB avail)"
if [ "${SWAP_TOTAL:-0}" -gt 0 ]; then
    ok "Swap    : ${SWAP_TOTAL}MB ✅"
else
    warn "Swap    : 未配置 ⚠️"
fi
DISK=$(df -h / | awk 'NR==2{print $4" avail ("$5" used)"}')
echo "磁盘    : $DISK"
echo ""

# ── [8] 时区 ──────────────────────────────────────────────
echo -e "${CYAN}--- [8] 时区 ---${NC}"
TZ_CUR=$(timedatectl show --property=Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null)
if echo "$TZ_CUR" | grep -qE "America/Los_Angeles|America/Pacific"; then
    ok "时区    : $TZ_CUR ✅"
else
    warn "时区    : $TZ_CUR ⚠️  建议: timedatectl set-timezone America/Los_Angeles"
fi
PT_TIME=$(TZ="America/Los_Angeles" date '+%H:%M')
echo "PT时间  : $PT_TIME"
echo ""

# ── 汇总 ──────────────────────────────────────────────────
echo -e "${CYAN}============================================${NC}"
if [ $ERRORS -gt 0 ]; then
    echo -e "${RED}  ❌  $ERRORS 个错误，$WARNINGS 个警告${NC}"
elif [ $WARNINGS -gt 0 ]; then
    echo -e "${YELLOW}  ⚠️   无错误，$WARNINGS 个警告${NC}"
else
    echo -e "${GREEN}  ✅  全部正常${NC}"
fi
echo -e "${CYAN}  检查时间: $(date '+%Y-%m-%d %H:%M:%S')${NC}"
echo -e "${CYAN}============================================${NC}"

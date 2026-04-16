#!/bin/bash
# ISP 初始化脚本。
#
# 模式：
#   LIGHTWEIGHT=1 ./setup_isp.sh   # 跳过 torch/transformers 重依赖，配合 DO 远程 embedding
#   ./setup_isp.sh                 # 完整安装（本地计算 embedding）
set -e

LIGHTWEIGHT=${LIGHTWEIGHT:-0}

# 1. 时区改为洛杉矶
timedatectl set-timezone America/Los_Angeles

# 2. 关闭密码登录（密钥已配好）
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart sshd

# 3. 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> /root/.bashrc

# 4. 克隆 Fiam
git clone https://github.com/Zephyr-Ovo/Fiam.git /root/Fiam

# 5. 配置 Fiam 环境
cd /root/Fiam
uv venv --python 3.12
source .venv/bin/activate
if [ "$LIGHTWEIGHT" = "1" ]; then
    # 只装运行时必需的轻量依赖；embedding 走远端
    uv pip install numpy python-frontmatter pyyaml anthropic python-dotenv \
        httpx rich fastapi uvicorn
    uv pip install -e . --no-deps
else
    # 完整安装（本地 embedding）
    uv pip install -e .
fi
cp fiam.toml.example fiam.toml

# 6. 配置 Fiam remote embedding（指向 DO，通过 SSH 隧道）
sed -i 's/embedding_backend = "local"/embedding_backend = "remote"/' fiam.toml
sed -i 's|embedding_remote_url = ""|embedding_remote_url = "http://127.0.0.1:8819"|' fiam.toml

# 7. 加 1GB Swap
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "=== ISP 初始化完成 (mode=$([ "$LIGHTWEIGHT" = "1" ] && echo light || echo full)) ==="
free -m | grep Swap
timedatectl | grep "Time zone"

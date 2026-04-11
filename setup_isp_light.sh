#!/bin/bash
# ISP 专用轻量安装 — remote embedding 模式不需要 torch/transformers
set -e

export PATH="$HOME/.local/bin:$PATH"

cd /root/Fiam

# 清理之前失败的 venv
rm -rf .venv

# 创建新 venv
uv venv --python 3.12

source .venv/bin/activate

# 只装 fiam 需要的非 ML 依赖（ISP 用 remote embedding，不需要 torch/transformers）
uv pip install \
    numpy \
    python-frontmatter \
    pyyaml \
    anthropic \
    python-dotenv \
    httpx \
    rich \
    fastapi \
    uvicorn

# 以 no-deps 方式装 fiam 本身（跳过它的 ML 依赖）
uv pip install -e . --no-deps

echo "=== 轻量安装完成 ==="
python -c "import fiam; print('fiam import OK')"

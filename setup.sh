#!/bin/bash
# fiam — per-user environment setup
# Run this once per user account.
# .venv is gitignored and user-specific — each user needs their own.

set -e

echo ""
echo "  fiam — environment setup"
echo ""

# Check uv is available
if ! command -v uv &> /dev/null; then
    echo "  Error: uv not found. Install from https://docs.astral.sh/uv/"
    exit 1
fi

# Recreate .venv for current user
echo "  Creating .venv for $(whoami) ..."
uv venv --python 3.11 --clear .venv

# Install dependencies
echo "  Installing dependencies ..."
uv sync

# Set HF_HOME for this session
export HF_HOME="$(pwd)/.cache/huggingface"

echo ""
echo "  Done. .venv ready for $(whoami)"
echo "  Next: uv run python scripts/fiam.py init"
echo ""

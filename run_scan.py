#!/usr/bin/env python3
"""One-shot scan script for ISP. Run from fiam-code root."""
import sys, os, traceback

# Ensure fiam package is importable before adding scripts/
# (scripts/fiam.py would shadow the fiam package otherwise)
import fiam.config  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))
from fiam_lib.storage import cmd_scan
from types import SimpleNamespace

args = SimpleNamespace(config=None, force=True, debug=True)
try:
    cmd_scan(args)
except Exception as e:
    traceback.print_exc()
    sys.exit(1)

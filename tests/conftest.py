from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"

for path in (str(SRC), str(SCRIPTS)):
    while path in sys.path:
        sys.path.remove(path)
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SRC))

loaded = sys.modules.get("fiam")
if loaded is not None and not hasattr(loaded, "__path__"):
    del sys.modules["fiam"]
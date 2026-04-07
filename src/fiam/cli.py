"""Console-script entry point for ``fiam``.

Bootstraps sys.path so that scripts/fiam_lib is importable, then
delegates to the argparse machinery in scripts/fiam.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def main() -> None:
    # fiam_lib lives in scripts/; the core package lives in src/.
    _root = Path(__file__).resolve().parent.parent.parent
    _scripts = str(_root / "scripts")
    if _scripts not in sys.path:
        sys.path.insert(0, _scripts)

    # Load scripts/fiam.py via importlib to avoid name collision
    # with the ``fiam`` package itself.
    spec = importlib.util.spec_from_file_location(
        "_fiam_dispatch", _root / "scripts" / "fiam.py",
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    mod.main()

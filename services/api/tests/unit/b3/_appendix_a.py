"""Test-time bootstrap for `app.core.contracts` (B3-owned, tests only).

B1 materializes `app/core/contracts.py` verbatim from `goal.md` Appendix A. Until that
commit lands, B3's tests would be unable to import the frozen contracts at all.

This module does NOT write anything into B1's directory. It extracts the Appendix A
python block straight out of `goal.md` — the authoritative source B1 itself copies from —
and registers it as `app.core.contracts` in `sys.modules`, but ONLY if the real module is
not importable. The moment B1's commit lands, this becomes a no-op.

There is exactly one copy of the contracts in this repo and it is `goal.md`. That is the
point: no drift is possible because nothing is duplicated.
"""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
API_ROOT = Path(__file__).resolve().parents[3]
GOAL_MD = REPO_ROOT / "goal.md"

_APPENDIX_HEADING = "## Appendix A"


def _extract_appendix_a_source() -> str:
    text = GOAL_MD.read_text(encoding="utf-8")
    idx = text.find(_APPENDIX_HEADING)
    if idx == -1:
        raise RuntimeError(f"{GOAL_MD} has no '{_APPENDIX_HEADING}' section")
    block = re.search(r"```python\n(.*?)\n```", text[idx:], re.DOTALL)
    if block is None:
        raise RuntimeError(f"{GOAL_MD} Appendix A contains no python block")
    return block.group(1)


def ensure_contracts_importable() -> None:
    """Make `app.core.contracts` importable. Prefers B1's real module."""
    if str(API_ROOT) not in sys.path:
        sys.path.insert(0, str(API_ROOT))

    real = API_ROOT / "app" / "core" / "contracts.py"
    if real.exists():
        return  # B1 has landed; use the real thing.

    if "app.core.contracts" in sys.modules:
        return

    # `app` and `app.core` resolve as PEP 420 namespace packages from API_ROOT.
    module = types.ModuleType("app.core.contracts")
    module.__file__ = str(GOAL_MD) + "#appendix-a"
    exec(compile(_extract_appendix_a_source(), module.__file__, "exec"), module.__dict__)

    import app.core  # noqa: F401  (namespace package, exists on disk)

    sys.modules["app.core.contracts"] = module
    sys.modules["app.core"].contracts = module

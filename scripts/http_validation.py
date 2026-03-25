from __future__ import annotations

import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
BEDROCK_ROOT = WORKSPACE_ROOT / "bedrock"

for candidate in (WORKSPACE_ROOT, BEDROCK_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from bedrock.scripts.run_http_validation import main


if __name__ == "__main__":
    raise SystemExit(main())

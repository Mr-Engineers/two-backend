"""Regenerate docs/seed-data.md from the seed dataset."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.seed.docs import DOC_PATH, render_seed_markdown  # noqa: E402

DOC_PATH.write_text(render_seed_markdown(), encoding="utf-8")
print(f"wrote {DOC_PATH}")

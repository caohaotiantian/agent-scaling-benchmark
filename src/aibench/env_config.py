"""Load local .env without requiring python-dotenv as a hard dep if missing."""

from __future__ import annotations

import os
from pathlib import Path

from aibench.io_util import repo_root


def load_dotenv(path: Path | None = None, *, override: bool = False) -> Path | None:
    """Parse KEY=VALUE lines into os.environ. Returns path loaded or None."""
    candidates: list[Path] = []
    if path is not None:
        candidates.append(path)
    else:
        root = repo_root()
        candidates.extend([root / ".env", Path.cwd() / ".env"])

    for p in candidates:
        if not p.is_file():
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            if not key:
                continue
            if override or key not in os.environ:
                os.environ[key] = val
        return p
    return None


def openai_settings() -> dict[str, str | None]:
    return {
        "api_key": os.environ.get("OPENAI_API_KEY") or os.environ.get("AIBENCH_API_KEY"),
        "base_url": os.environ.get("OPENAI_BASE_URL") or os.environ.get("AIBENCH_BASE_URL"),
        "model": os.environ.get("OPENAI_MODEL") or os.environ.get("AIBENCH_MODEL"),
    }

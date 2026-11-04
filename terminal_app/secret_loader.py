"""Read the key set by the local DPAPI PowerShell launcher."""

from __future__ import annotations

import os
from pathlib import Path


def load_deepseek_key(repository: Path) -> str | None:
    del repository
    return os.environ.get("DEEPSEEK_API_KEY")

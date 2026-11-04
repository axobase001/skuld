"""Local profile paths. Profiles share application code, never V4 state or Pi roots."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


PROFILE_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")


@dataclass(frozen=True)
class LocalAccount:
    name: str
    runtime: Path
    workspace: Path
    secret: Path


def account_paths(repository: Path, name: str, default_workspace: Path | None = None) -> LocalAccount:
    if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
        raise ValueError("Profile must be 1-32 lowercase letters, digits, '_' or '-', starting with a letter")
    repository = repository.resolve(strict=True)
    if name == "default":
        workspace = (default_workspace or repository).resolve(strict=True)
        protected = (repository / ".terminal-accounts", repository / ".terminal-runtime",
                     repository / ".skuld-terminal-secrets")
        if any(workspace == item or item in workspace.parents for item in protected):
            raise ValueError("Default workspace cannot point inside a profile, runtime or secret directory")
        return LocalAccount(name, repository / ".terminal-runtime", workspace,
                            repository / ".skuld-terminal-secrets" / "deepseek.dpapi")
    if default_workspace is not None:
        raise ValueError("A named profile uses its private workspace; -Workspace applies only to default")
    base = repository / ".terminal-accounts" / name
    return LocalAccount(name, base / "runtime", base / "workspace", base / "deepseek.dpapi")

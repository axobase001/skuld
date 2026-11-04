"""Time, id and JSON primitives shared by app, loop and policy.

Split out of skuld/app/runtime.py in S0 only to break the runtime <-> product_loop
import cycle once function-level imports were hoisted (D1: no lazy imports).
The definitions are unchanged.
S1 adds ensure_utc_iso, new_id and utc_now, copied verbatim from skuld/legacy/personal_model/
schemas.py when skuld/legacy was removed.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import uuid

from skuld.kernel.schemas import canonical_hash

def now():return datetime.now(timezone.utc).isoformat()
def instant(s):
    d=datetime.fromisoformat(s.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('timezone required')
    return d.astimezone(timezone.utc)
def nid(prefix,*values):return prefix+'_'+canonical_hash(values)[:40]
def dumps(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,default=str)


@dataclass(frozen=True)
class ProviderRequest:
    user_id:str
    stage:str
    prompt:str
    model_class:object
    ref:str
    schema_override:object=None
    validate:object=None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def ensure_utc_iso(value: str | datetime | None, *, default_now: bool = False) -> str | None:
    if value is None or value == "":
        return utc_now() if default_now else None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"

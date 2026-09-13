"""The mandate on disk: validate and write owed/mandate/mandate.yaml. The UI's mandate form calls
update_mandate(); the next run picks it up because load_mandate() reads the file every time.

update_mandate(changes) -> dict     merges `changes` over the current file, validates, writes atomically
validate_mandate(mandate) -> None   raises ValueError with every problem listed

Editable keys: signoff (str), tone (list of str), never (list of str), approval (str),
step_1 / step_2 / step_3 ({day: int, action: str, requires_tap?: bool}, days strictly increasing).
Anything else is rejected. `steps` is always 3.
"""
from __future__ import annotations
import os
import tempfile
from pathlib import Path
from typing import Optional

import yaml

from owed.config import ROOT

MANDATE_PATH = ROOT / "owed" / "mandate" / "mandate.yaml"
STEP_KEYS = ("step_1", "step_2", "step_3")
EDITABLE = {"signoff", "tone", "never", "approval", *STEP_KEYS}


def _str_list(name: str, value, problems: list[str], max_items: int = 12) -> None:
    if not isinstance(value, list) or not value:
        problems.append(f"{name} must be a non-empty list of short phrases")
        return
    if len(value) > max_items:
        problems.append(f"{name} has {len(value)} items; at most {max_items}")
    for v in value:
        if not isinstance(v, str) or not v.strip() or len(v) > 120 or "\n" in v:
            problems.append(f"{name} entry {v!r} must be one line of text under 120 characters")


def validate_mandate(mandate: dict) -> None:
    problems: list[str] = []
    if not isinstance(mandate, dict):
        raise ValueError("mandate must be a mapping")
    unknown = set(mandate) - EDITABLE - {"steps", "voice"}
    if unknown:
        problems.append(f"unknown keys {sorted(unknown)}")
    if mandate.get("steps", 3) != 3:
        problems.append("steps must be 3")
    for key in ("signoff", "approval", "voice"):
        if key in mandate and (not isinstance(mandate[key], str) or not mandate[key].strip() or len(mandate[key]) > 120):
            problems.append(f"{key} must be one line of text under 120 characters")
    _str_list("tone", mandate.get("tone"), problems)
    _str_list("never", mandate.get("never"), problems)
    days: list[int] = []
    for key in STEP_KEYS:
        step = mandate.get(key)
        if not isinstance(step, dict):
            problems.append(f"{key} must be a mapping with day and action")
            continue
        extra = set(step) - {"day", "action", "requires_tap"}
        if extra:
            problems.append(f"{key} has unknown keys {sorted(extra)}")
        day = step.get("day")
        if not isinstance(day, int) or isinstance(day, bool) or not 1 <= day <= 365:
            problems.append(f"{key}.day must be a whole number of days from 1 to 365")
        else:
            days.append(day)
        action = step.get("action")
        if not isinstance(action, str) or not action.strip() or len(action) > 120:
            problems.append(f"{key}.action must be one line of text under 120 characters")
        if "requires_tap" in step and not isinstance(step["requires_tap"], bool):
            problems.append(f"{key}.requires_tap must be true or false")
    if len(days) == 3 and not (days[0] < days[1] < days[2]):
        problems.append(f"step days must increase: got {days}")
    if isinstance(mandate.get("step_3"), dict) and mandate["step_3"].get("requires_tap") is False:
        problems.append("step_3.requires_tap cannot be false: nothing past step 2 sends without the tap")
    if problems:
        raise ValueError("; ".join(problems))


def read_mandate(path: Optional[Path] = None) -> dict:
    data = yaml.safe_load(Path(path or MANDATE_PATH).read_text())
    if not isinstance(data, dict):
        raise ValueError("mandate.yaml must contain a mapping")
    return data


def update_mandate(changes: dict, path: Optional[Path] = None) -> dict:
    """Merge `changes` over the current mandate, validate, write atomically, return the new mandate.
    A step is replaced whole (pass day and action together). Nothing is written if validation fails."""
    path = Path(path or MANDATE_PATH)
    if not isinstance(changes, dict):
        raise ValueError("changes must be a mapping")
    unknown = set(changes) - EDITABLE
    if unknown:
        raise ValueError(f"cannot change {sorted(unknown)}; editable keys are {sorted(EDITABLE)}")
    current = read_mandate(path)
    merged = {**current, **changes, "steps": 3}
    validate_mandate(merged)
    text = "# The mandate. Set once by the freelancer; edited through update_mandate(), which validates.\n" + \
        yaml.safe_dump(merged, sort_keys=False, allow_unicode=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mandate-", suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    return merged

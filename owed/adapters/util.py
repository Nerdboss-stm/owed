"""Helpers shared by adapters. stdlib only so Shadow adapters may import it."""
from __future__ import annotations
import time
from typing import Callable, TypeVar

T = TypeVar("T")


def retry_read(fn: Callable[[], T], attempts: int = 3, base_delay: float = 1.0) -> T:
    """Retries are for READS only. Never wrap a write in this (retries create duplicates)."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - re-raised below, never swallowed
            last = e
            if i < attempts - 1:
                time.sleep(base_delay * (2 ** i))
    assert last is not None
    raise last

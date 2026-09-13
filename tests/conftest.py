import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals import harness  # noqa: E402

# Every AC test needs the agent. Until the core merge lands they xfail with the import error.
needs_agent = pytest.mark.xfail(
    not harness.agent_available(),
    reason=harness.missing_reason() or "agent present",
    strict=True,
)


@pytest.fixture
def run_dirs(tmp_path):
    """(state_dir, traces_dir) unique to one test."""
    return tmp_path / "state", tmp_path / "traces"

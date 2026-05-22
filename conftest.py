"""
conftest.py — Project root
Adds project root to sys.path so all "from sentinel.xxx import yyy"
imports work correctly when running pytest from the project root.
"""
import sys
import uuid
from pathlib import Path

import pytest

# This file lives at sentinel_project/conftest.py
# Adding its parent (sentinel_project/) to sys.path
# lets Python find the sentinel/ package inside it
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def tmp_path() -> Path:
    """
    Workspace-local tmp_path replacement.

    Python 3.14 + pytest on this Windows machine leaves temp lock files open
    under pytest's numbered temp root, causing PermissionError during fixture
    setup before the real tests run. A simple unique workspace directory gives
    tests an isolated path without relying on that cleanup path.
    """
    root = Path(__file__).parent / ".test_tmp"
    root.mkdir(exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    return path

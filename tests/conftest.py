from __future__ import annotations

from pathlib import Path

import pytest

from steadygate.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "history.db")
    yield s  # type: ignore[misc]
    s.close()


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES

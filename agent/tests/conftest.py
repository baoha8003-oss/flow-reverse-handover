import os
import tempfile
from pathlib import Path

import pytest

# Point Flowboard at an isolated temp dir BEFORE importing the app.
_TMPDIR = tempfile.mkdtemp(prefix="flowboard-test-")
os.environ["FLOWBOARD_STORAGE"] = _TMPDIR
os.environ["FLOWBOARD_DB"] = str(Path(_TMPDIR) / "test.db")
# Force the deterministic mock planner in tests — never spawn `claude` subprocess.
# Individual tests that want to exercise the CLI path patch the module directly.
os.environ["FLOWBOARD_PLANNER_BACKEND"] = "mock"
# Pin the worker to serial, no-cooldown so existing tests stay deterministic
# and fast. Tests that exercise concurrency construct a WorkerController with
# explicit max_concurrent / cooldown_s overrides.
os.environ["FLOWBOARD_MAX_CONCURRENT"] = "1"
os.environ["FLOWBOARD_API_COOLDOWN_S"] = "0"
# Cut the suite off from the packaged Gemini key list. ASSET_ROOT deliberately
# points at the real library (postprod tests run the real ffmpeg against its
# real fonts), so this file would otherwise hand every test run the developer's
# own live keys — the suite would spend real quota and make live API calls, and
# "is this provider available?" would answer differently on every machine.
# Tests that exercise the file source point this at a temp file of their own.
os.environ["FLOWBOARD_GEMINI_KEY_FILE"] = ""
# Same reasoning for the ambient key env vars.
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from flowboard.db.session import engine  # noqa: E402
from flowboard.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db():
    """Drop + recreate all tables before each test so state is isolated."""
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _seed_default_paygate_tier():
    """Most tests exercise downstream behaviour (variant_count, ref_media_ids,
    SDK payload shape, etc.) and don't care about the upstream tier-resolution
    chain. Pre-Phase-1, the worker silently defaulted to PAYGATE_TIER_ONE when
    no signal was present, so tests didn't have to think about tier at all.
    Phase 1 made that fail loud — every gen now requires a tier signal — so
    we keep the test-time ergonomics by simulating the "extension already
    sniffed Pro" state by default. Tests that specifically want to exercise
    the no-tier path (e.g. test_processor_tier_fallback.py) reset the cache
    in their own module-local autouse fixture, which runs after this one and
    wins.
    """
    from flowboard.services.flow_client import flow_client
    flow_client._paygate_tier = "PAYGATE_TIER_ONE"
    yield
    flow_client._paygate_tier = None


@pytest.fixture
def client():
    return TestClient(app)

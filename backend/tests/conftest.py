"""Shared fixtures for service tests against a throwaway SQLite database."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="workbench-tests-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP / 'test.db'}")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret")
os.environ.setdefault("ARTIFACTS_DIR", str(_TMP / "artifacts"))
os.environ.setdefault("SEED_DEMO", "false")
(Path(os.environ["ARTIFACTS_DIR"])).mkdir(parents=True, exist_ok=True)

import os
from pathlib import Path

from aibench.env_config import load_dotenv, openai_settings


def test_load_dotenv_file(tmp_path: Path, monkeypatch):
    envf = tmp_path / ".env"
    envf.write_text("UNITTEST_ONLY_KEY=hello123\nOPENAI_MODEL=TestModel\n", encoding="utf-8")
    monkeypatch.delenv("UNITTEST_ONLY_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    loaded = load_dotenv(envf, override=True)
    assert loaded == envf
    assert os.environ.get("UNITTEST_ONLY_KEY") == "hello123"
    assert openai_settings()["model"] == "TestModel"


def test_load_dotenv_missing(tmp_path: Path):
    assert load_dotenv(tmp_path / "nope.env") is None

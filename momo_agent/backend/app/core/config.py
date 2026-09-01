from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
APP_VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class Settings:
    app_name: str = "MOMO Bridge Analysis And Optimization Platform"
    api_prefix: str = "/api"
    version: str = APP_VERSION


settings = Settings()

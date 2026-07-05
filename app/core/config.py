from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    app_name: str = "AFSIM_LLM"
    host: str = os.getenv("AFSIM_LLM_HOST", "127.0.0.1")
    port: int = int(os.getenv("AFSIM_LLM_PORT", "8000"))
    db_path: Path = Path(os.getenv("AFSIM_LLM_DB", str(PROJECT_ROOT / "afsim_llm.sqlite3")))
    afsim_root: Path = Path(
        os.getenv(
            "AFSIM_ROOT",
            r"D:\AFISM\AFSIM\am-2.9.0-win64.part1\afsim-2.9.0-win64",
        )
    )
    siliconflow_api_key: str = os.getenv("SILICONFLOW_API_KEY", "")
    siliconflow_base_url: str = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    siliconflow_model: str = os.getenv("SILICONFLOW_MODEL", "Pro/zai-org/GLM-4.7")
    llm_timeout_seconds: float = float(os.getenv("AFSIM_LLM_TIMEOUT", "25"))


settings = Settings()

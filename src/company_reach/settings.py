"""Typed configuration read from .env / environment.

pydantic-settings turns environment variables into one validated object, so
the rest of the code never touches os.environ. Secrets are SecretStr: they
do not appear in repr() or logs.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    lindas_url: str = "https://lindas.admin.ch/query"

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr
    llm_model: str
    llm_structured_method: Literal["json_schema", "function_calling", "json_mode"] = (
        "json_schema"
    )
    llm_concurrency: int = 5

    searxng_url: str = "http://searxng:8080"
    serper_api_key: SecretStr | None = None
    playwright_url: str | None = None

    max_pages_per_site: int = 10
    max_chars_per_page: int = 8000
    score_batch_size: int = 50
    max_batches_per_run: int = 3
    langsmith_tracing: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "company_reach.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()

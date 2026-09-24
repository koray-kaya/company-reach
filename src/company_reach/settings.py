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
    profile_path: Path = Path("profile.toml")
    # The ethics gate (M7 open point 1): Send stays locked until a human who
    # knows approval was granted sets this. Only a person can know it.
    sending_approved: bool = False
    lindas_url: str = "https://lindas.admin.ch/query"
    shab_url: str = "https://www.shab.ch/api/v1"

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr
    llm_model: str
    llm_structured_method: Literal["json_schema", "function_calling", "json_mode"] = (
        "json_schema"
    )
    # GLM-5.3 cannot switch thinking off; "low" is the least it will do, and
    # measured most self-consistent for scoring. Harder prompts override this
    # per call.
    llm_reasoning_effort: Literal["low", "high", "max"] = "low"
    # Measured need for 50 companies is ~1.3k-5k tokens; 8k truncated under
    # load. Finite so a runaway generation fails in minutes, not an hour:
    # at the measured ~38 tokens/s this cap is reached in about 22 minutes.
    llm_max_tokens: int = 50000
    # A scoring call measured 41-130 s; httpx defaults to 5 s.
    llm_timeout_seconds: float = 600.0
    # Ten concurrent requests timed out four of ten on the shared endpoint
    # without raising throughput.
    llm_concurrency: int = 3

    searxng_url: str = "http://searxng:8080"
    serper_api_key: SecretStr | None = None
    # Ten children starting find_site at once would fire 30-40 queries from
    # one IP in the first seconds, which is the pattern that gets an engine
    # suspended. The fetcher's per-host delay does not cover these.
    search_concurrency: int = 2
    search_gap_s: float = 1.0
    # When every query for one company came back empty, wait this long and
    # ask again. Long enough for an engine's short suspension to lapse; the
    # 2026-09-21 capture got its sites back on a rerun minutes later.
    search_retry_pause_s: float = 30.0
    # The engines that must all be unresponsive before an empty result stops
    # meaning "this company has no website". Google is a bonus, not baseline:
    # measured, a self-hosted SearXNG behaves like a DuckDuckGo proxy.
    baseline_engines: str = "duckduckgo,mojeek,brave"
    playwright_url: str | None = None

    max_pages_per_site: int = 10
    max_page_urls: int = 200
    max_chars_per_page: int = 8000
    # A Wix home page is 0.8-1.5 MB of HTML; two megabytes is generous and
    # still bounds what one bad URL can cost.
    max_page_bytes: int = 2_000_000
    # Politeness between two requests to the same host. A robots.txt
    # Crawl-delay replaces this rather than adding to it.
    per_host_delay_s: float = 1.0
    score_batch_size: int = 50
    # Scoring is incremental: one pass takes this many companies and stops,
    # so work can start long before the whole pool is scored.
    score_limit: int = 500
    # How many companies one batch draws. Ten, because review starts once ten
    # candidates are ready (design doc, "Candidate selection").
    batch_size: int = 10
    # A company scoring below this is never drawn, so pool_exhausted means
    # "nothing left that clears the bar" rather than "nothing left at all".
    # Seven sits in a measured gap: on the golden set the companies Koray
    # hand-rated 6 or above score 9, 9, 9 and 3, while nothing he rated 3 or
    # below scores above 6. Seven separates them with room for the +/-1 noise
    # a single score carries; nine would sit exactly on the good ones.
    draw_min_score: int = 7
    max_batches_per_run: int = 3
    langsmith_tracing: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "company_reach.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()

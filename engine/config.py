"""Typed engine configuration. Every limit is configurable; nothing is assumed sufficient.

Environment overrides use ENGINE_<SECTION>_<FIELD>, e.g. ENGINE_BUDGET_MAX_FETCH_ATTEMPTS=40.
The contact for the User-Agent comes from ENGINE_CONTACT (falling back to PERSONALIZER_CONTACT).
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
BOT_TOKEN = "PersonalizerResearchBot"


class Budget(BaseModel):
    max_leads: int = Field(200, ge=1)
    max_search_requests: int = Field(40, ge=0)
    max_fetch_attempts: int = Field(80, ge=0)  # every HTTP request for content, retries included
    max_robots_requests: int = Field(40, ge=0)
    max_total_bytes: int = Field(25_000_000, ge=0)
    max_wall_seconds: float = Field(240.0, gt=0)
    max_cost_usd: float = Field(0.25, ge=0)


class FetchSettings(BaseModel):
    connect_timeout: float = 5.0
    read_timeout: float = 10.0
    total_timeout: float = 20.0  # per request, including body download
    max_retries: int = Field(2, ge=0)
    backoff_base: float = 0.5
    backoff_max: float = 8.0
    retry_after_cap: float = 30.0  # longer Retry-After values are not waited for
    max_redirects: int = Field(5, ge=0)
    max_page_bytes: int = 3_000_000
    per_host_concurrency: int = Field(2, ge=1)
    global_concurrency: int = Field(6, ge=1)
    min_host_interval: float = 1.0  # politeness; raised by robots Crawl-delay (capped)
    max_crawl_delay: float = 10.0


class ExtractSettings(BaseModel):
    min_usable_chars: int = 200  # articles and pages
    min_record_chars: int = 30  # feed entries and short-form records
    js_shell_min_html_bytes: int = 15_000
    max_text_chars: int = 200_000
    max_feed_entries: int = 50
    near_duplicate_hamming: int = 3


class Retention(BaseModel):
    run_days: int = 30
    text_days: int = 7


class Platforms(BaseModel):
    """Connector switches (phase 4). Off by default where terms need review (DECISIONS D2.3, D2.4)."""

    x_oembed_enabled: bool = False
    reddit_oembed_enabled: bool = False


class Providers(BaseModel):
    """Search providers. Monthly caps keep usage inside the free allowances (decision: no paid billing).

    Caps count this engine's own usage from the provider_ledger table (calendar month, UTC). Usage made
    elsewhere with the same keys is invisible to the ledger, so caps sit below the free allowance.
    """

    enabled: list[str] = Field(default_factory=lambda: ["brave", "tavily", "exa"])
    brave_api_key: str | None = None
    tavily_api_key: str | None = None
    exa_api_key: str | None = None
    brave_monthly_cap_requests: float = 900  # free credit $5/month = 1,000 requests at $5/1k
    tavily_monthly_cap_credits: float = 900  # free plan 1,000 credits/month
    exa_monthly_cap_usd: float = 9.0  # free credit $10/month
    timeout: float = 20.0
    max_retries: int = 2
    results_per_query: int = Field(10, ge=1, le=20)


class EngineConfig(BaseModel):
    db_path: Path = ROOT / "data" / "engine.db"
    contact: str | None = None
    budget: Budget = Budget()
    fetch: FetchSettings = FetchSettings()
    extract: ExtractSettings = ExtractSettings()
    retention: Retention = Retention()
    platforms: Platforms = Platforms()
    providers: Providers = Providers()

    @property
    def user_agent(self) -> str:
        contact = f"; +{self.contact}" if self.contact else ""
        return f"{BOT_TOKEN}/0.2 (public-information research; respects robots.txt{contact})"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, env_file: Path | None = ROOT / ".env") -> "EngineConfig":
        values = dict(_read_env_file(env_file)) if env_file else {}
        values.update(os.environ if env is None else env)
        cfg = cls()
        if values.get("ENGINE_DB_PATH"):
            cfg.db_path = Path(values["ENGINE_DB_PATH"])
        cfg.contact = values.get("ENGINE_CONTACT") or values.get("PERSONALIZER_CONTACT") or None
        for section in ("budget", "fetch", "extract", "retention", "platforms", "providers"):
            model = getattr(cfg, section)
            updates = {}
            for name, field in type(model).model_fields.items():
                key = f"ENGINE_{section.upper()}_{name.upper()}"
                if key in values and values[key] != "":
                    updates[name] = [v.strip() for v in values[key].split(",") if v.strip()] \
                        if name == "enabled" else values[key]
            if updates:
                setattr(cfg, section, type(model).model_validate({**model.model_dump(), **updates}))
        # provider keys: reuse the existing variable names from .env (never logged or stored)
        for name in ("brave", "tavily", "exa"):
            key = values.get(f"{name.upper()}_API_KEY")
            if key:
                setattr(cfg.providers, f"{name}_api_key", key)
        return cfg

    def safe_dump(self) -> dict:
        """Config snapshot for storage: API keys are never written to the database."""
        data = self.model_dump(mode="json")
        for name in ("brave", "tavily", "exa"):
            data["providers"][f"{name}_api_key"] = bool(getattr(self.providers, f"{name}_api_key"))
        return data


def _read_env_file(path: Path):
    """Read KEY=VALUE pairs without modifying os.environ. Inline ' #' comments are stripped."""
    if not path or not Path(path).is_file():
        return
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in {'"', "'"} and value[:1] in value[1:]:
            value = value[1 : value.index(value[0], 1)]
        else:
            value = value.split(" #", 1)[0].strip()
            if value.startswith("#"):
                value = ""
        yield key.strip(), value

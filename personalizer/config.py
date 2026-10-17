"""Settings loaded from environment variables (and an optional local .env file).

Secrets are only ever read from the environment; nothing is hardcoded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# USD per 1M tokens (input, output). Keep in sync with provider pricing pages.
LLM_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

# USD per search request at pay-as-you-go rates (free tiers cover small volumes).
SEARCH_PRICES: dict[str, float] = {
    "tavily": 0.008,
    "exa": 0.007,
    "brave": 0.005,
    "wikipedia": 0.0,
    "fixture": 0.0,
}


def load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines; existing environment wins."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in {'"', "'"} and value[:1] in value[1:]:
            value = value[1 : value.index(value[0], 1)]  # quoted: keep everything inside the quotes
        else:
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()  # strip inline comment
            if value.startswith("#"):
                value = ""
        if value:
            os.environ.setdefault(key.strip(), value)


def _list(value: str | None) -> list[str]:
    return [v.strip().lower() for v in (value or "").split(",") if v.strip()]


@dataclass
class Settings:
    search_providers: list[str] = field(default_factory=lambda: ["wikipedia"])
    llm_provider: str = "none"  # "anthropic" | "none"
    model: str = "claude-opus-5-5"
    llm_effort: str = "low"
    max_cost_usd: float = 0.75
    db_path: Path = PROJECT_ROOT / "data" / "personalizer.db"
    retention_days: int = 30
    max_fetches: int = 12
    results_per_query: int = 5
    fetch_timeout_s: float = 10.0
    max_page_bytes: int = 2_000_000
    contact: str | None = None  # URL or email for the User-Agent; Wikimedia requires one
    api_token: str | None = None
    fixture_path: Path = PROJECT_ROOT / "eval" / "fixtures" / "corpus.json"
    extra_reputable_domains: list[str] = field(default_factory=list)
    extra_blocked_domains: list[str] = field(default_factory=list)
    tavily_api_key: str | None = None
    exa_api_key: str | None = None
    brave_api_key: str | None = None
    brave_storage_rights: bool = False

    @classmethod
    def from_env(cls, env_file: Path | None = PROJECT_ROOT / ".env") -> "Settings":
        if env_file:
            load_dotenv(env_file)
        e = os.environ.get
        s = cls()
        s.search_providers = _list(e("PERSONALIZER_SEARCH_PROVIDERS")) or s.search_providers
        s.llm_provider = (e("PERSONALIZER_LLM") or s.llm_provider).lower()
        s.model = e("PERSONALIZER_MODEL") or s.model
        s.llm_effort = e("PERSONALIZER_LLM_EFFORT") or s.llm_effort
        s.max_cost_usd = float(e("PERSONALIZER_MAX_COST_USD") or s.max_cost_usd)
        s.db_path = Path(e("PERSONALIZER_DB_PATH") or s.db_path)
        s.retention_days = int(e("PERSONALIZER_RETENTION_DAYS") or s.retention_days)
        s.max_fetches = int(e("PERSONALIZER_MAX_FETCHES") or s.max_fetches)
        s.results_per_query = int(e("PERSONALIZER_RESULTS_PER_QUERY") or s.results_per_query)
        s.contact = e("PERSONALIZER_CONTACT") or None
        s.api_token = e("PERSONALIZER_API_TOKEN") or None
        s.fixture_path = Path(e("PERSONALIZER_FIXTURE_PATH") or s.fixture_path)
        s.extra_reputable_domains = _list(e("PERSONALIZER_EXTRA_REPUTABLE_DOMAINS"))
        s.extra_blocked_domains = _list(e("PERSONALIZER_EXTRA_BLOCKED_DOMAINS"))
        s.tavily_api_key = e("TAVILY_API_KEY") or None
        s.exa_api_key = e("EXA_API_KEY") or None
        s.brave_api_key = e("BRAVE_API_KEY") or None
        s.brave_storage_rights = (e("BRAVE_STORAGE_RIGHTS") or "").lower() == "true"
        return s

    @property
    def user_agent(self) -> str:
        contact = f"; +{self.contact}" if self.contact else ""
        return f"PersonalizerResearchBot/0.1 (meeting-prep prototype; respects robots.txt{contact})"

    def describe(self) -> dict:
        """Safe-to-display configuration summary (never includes key values)."""
        return {
            "search_providers": self.search_providers,
            "llm_provider": self.llm_provider,
            "model": self.model if self.llm_provider == "anthropic" else None,
            "retention_days": self.retention_days,
            "max_fetches": self.max_fetches,
            "max_cost_usd": self.max_cost_usd,
            "contact_configured": bool(self.contact),
            "keys_present": {
                "tavily": bool(self.tavily_api_key),
                "exa": bool(self.exa_api_key),
                "brave": bool(self.brave_api_key),
                "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
            },
        }

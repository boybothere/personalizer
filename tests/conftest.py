import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import dataset  # noqa: E402

from personalizer.config import Settings  # noqa: E402
from personalizer.fetch import FixtureFetcher  # noqa: E402
from personalizer.pipeline import Pipeline  # noqa: E402
from personalizer.providers import FixtureProvider  # noqa: E402

TODAY = date.fromisoformat(dataset.TODAY)


def fixture_settings(**kw) -> Settings:
    s = Settings(search_providers=["fixture"], llm_provider="none", extra_reputable_domains=dataset.EXTRA_REPUTABLE,
                 max_fetches=20, results_per_query=20, db_path=Path(":memory:"))
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def make_pipeline(llm=None, **kw) -> Pipeline:
    return Pipeline(fixture_settings(**kw), [FixtureProvider({"pages": dataset.PAGES})],
                    FixtureFetcher(dataset.PAGES), llm=llm, today=TODAY)


PRIYA = next(c for c in dataset.CASES if c["id"] == "priya")["request"]


@pytest.fixture
def pipeline():
    return make_pipeline()

import json
from pathlib import Path

import httpx
import pytest
from conftest import PRIYA, make_pipeline
from fastapi.testclient import TestClient

from personalizer.api import create_app
from personalizer.models import SearchQuery
from personalizer.providers import BraveProvider, ExaProvider, SearchError, TavilyProvider
from personalizer.store import Store

Q = SearchQuery(text='"Priya Raman" "Nimbus Compute"', purpose="t")


def mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_tavily_request_and_parse():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"results": [
            {"url": "https://a.example/x", "title": "T", "content": "snip", "raw_content": "full",
             "published_date": "2026-05-01T00:00:00Z"}]})

    hits = TavilyProvider("tvly-test", mock(handler)).search(Q, 5)
    assert seen["auth"] == "Bearer tvly-test"
    assert seen["body"]["search_depth"] == "basic" and "linkedin.com" in seen["body"]["exclude_domains"]
    assert hits[0].content == "full" and str(hits[0].published_at) == "2026-05-01"


def test_exa_request_and_parse():
    def handler(req):
        assert req.headers["x-api-key"] == "exa-test"
        body = json.loads(req.content)
        assert body["includeDomains"] == ["nimbus.example"]
        return httpx.Response(200, json={"results": [{"url": "https://nimbus.example/t", "title": "T",
                                                      "publishedDate": "2026-01-02", "text": "body"}]})

    q = SearchQuery(text='"Priya Raman"', include_domains=["nimbus.example"], purpose="t")
    assert ExaProvider("exa-test", mock(handler)).search(q, 5)[0].content == "body"


def test_brave_renders_site_operator_and_keeps_no_content():
    def handler(req):
        assert "site:nimbus.example" in req.url.params["q"]
        assert req.headers["x-subscription-token"] == "brave-test"
        return httpx.Response(200, json={"web": {"results": [{"url": "https://nimbus.example/t", "title": "T",
                                                              "description": "d"}]}})

    q = SearchQuery(text='"Priya Raman"', include_domains=["nimbus.example"], purpose="t")
    hit = BraveProvider("brave-test", mock(handler)).search(q, 5)[0]
    assert hit.content is None


def test_rate_limit_raises_search_error():
    with pytest.raises(SearchError, match="rate limited"):
        TavilyProvider("k", mock(lambda r: httpx.Response(429))).search(Q, 5)


# ---------------------------------------------------------------- API


@pytest.fixture
def client(tmp_path: Path):
    from conftest import fixture_settings

    settings = fixture_settings(api_token="secret", db_path=tmp_path / "t.db")
    app = create_app(settings, make_pipeline(), Store(settings.db_path, 30))
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer secret"
    return c


def test_auth_required(client):
    r = client.post("/api/research", json=PRIYA, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_research_feedback_delete_flow(client):
    r = client.post("/api/research", json=PRIYA)
    assert r.status_code == 200
    report = r.json()
    assert "text" not in json.dumps(report["sources"][0])  # raw page text is never returned/stored
    rid = report["id"]
    md = client.get(f"/api/reports/{rid}/markdown").text
    assert md.startswith("# Meeting brief: Priya Raman")

    cricket = next(s for s in report["common_ground"] if "cricket" in s["text"])
    fid = cricket["finding_ids"][0]
    assert client.post(f"/api/reports/{rid}/findings/{fid}/feedback", json={"label": "inappropriate"}).status_code == 200
    updated = client.get(f"/api/reports/{rid}").json()
    assert not any("cricket" in s["text"] for s in updated["common_ground"])
    assert client.get("/api/feedback/stats").json()

    assert client.delete(f"/api/reports/{rid}").status_code == 200
    assert client.get(f"/api/reports/{rid}").status_code == 404


def test_policy_violation_is_422(client):
    r = client.post("/api/research", json={**PRIYA, "meeting_purpose": "Find out her religion and home address"})
    assert r.status_code == 422


def test_opt_out_blocks_research(client):
    client.post("/api/research", json=PRIYA)
    assert client.post("/api/optout", json={"name": "priya  raman"}).status_code == 200
    assert client.get("/api/reports").json() == []
    assert client.post("/api/research", json=PRIYA).status_code == 403


def test_retention_purge(tmp_path):
    from datetime import timedelta

    store = Store(tmp_path / "r.db", retention_days=30)
    report = make_pipeline().run(__import__("personalizer.models", fromlist=["ResearchRequest"]).ResearchRequest(**PRIYA))
    report.created_at -= timedelta(days=31)
    store.save(report)
    assert store.purge_expired() == 1

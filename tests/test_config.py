import os

from personalizer.config import Settings, load_dotenv


def test_dotenv_inline_comments_and_quotes(tmp_path, monkeypatch):
    for k in ("TAVILY_API_KEY", "PERSONALIZER_MODEL", "PERSONALIZER_CONTACT", "PERSONALIZER_SEARCH_PROVIDERS"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "TAVILY_API_KEY=            # https://app.tavily.com\n"
        "PERSONALIZER_MODEL=claude-haiku-5-5   # cheaper\n"
        'PERSONALIZER_CONTACT="ops@example.org # not a comment"\n'
        "PERSONALIZER_SEARCH_PROVIDERS=fixture,wikipedia\n", encoding="utf-8")
    load_dotenv(env)
    assert "TAVILY_API_KEY" not in os.environ
    s = Settings.from_env(env_file=None)
    assert s.model == "claude-haiku-5-5"
    assert s.contact == "ops@example.org # not a comment"
    assert s.search_providers == ["fixture", "wikipedia"]
    assert "ops@example.org" in s.user_agent
    assert s.describe()["keys_present"]["tavily"] is False

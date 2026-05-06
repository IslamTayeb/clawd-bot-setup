import sys
from types import SimpleNamespace

from clawd_ops import search


def test_browse_web_returns_short_fallback_when_playwright_fails(monkeypatch):
    monkeypatch.setattr(search, "_fetch_text", lambda *_args, **_kwargs: "short text")
    fake_sync_api = SimpleNamespace(sync_playwright=lambda: (_ for _ in ()).throw(RuntimeError("playwright unavailable")))
    monkeypatch.setitem(sys.modules, "playwright", SimpleNamespace(sync_api=fake_sync_api))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    result = search.browse_web("https://example.com")
    assert result == "short text"


def test_search_arxiv_parses_feed(monkeypatch):
    class Response:
        text = """
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>https://arxiv.org/abs/1234.5678</id>
            <title>Test Paper</title>
            <summary>Summary text</summary>
            <author><name>Alice</name></author>
            <author><name>Bob</name></author>
          </entry>
        </feed>
        """

        def raise_for_status(self):
            return None

    monkeypatch.setattr(search.requests, "get", lambda *args, **kwargs: Response())
    results = search.search_arxiv("transformer", 1)
    assert results == [
        {
            "title": "Test Paper",
            "authors": ["Alice", "Bob"],
            "abstract": "Summary text",
            "url": "https://arxiv.org/abs/1234.5678",
        }
    ]


def test_search_web_parses_duckduckgo_results(monkeypatch):
    class Response:
        text = """
        <div class="result">
          <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fpaper">Example Result</a>
          <a class="result__snippet">Useful snippet</a>
        </div>
        """

        def raise_for_status(self):
            return None

    monkeypatch.setattr(search.requests, "get", lambda *args, **kwargs: Response())

    assert search.search_web("test query", 1) == [
        {
            "title": "Example Result",
            "url": "https://example.com/paper",
            "snippet": "Useful snippet",
        }
    ]


def test_search_github_repos_parses_api_response(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "items": [
                    {
                        "full_name": "owner/repo",
                        "description": "Repo description",
                        "html_url": "https://github.com/owner/repo",
                        "stargazers_count": 42,
                        "language": "Python",
                        "updated_at": "2026-03-10T00:00:00Z",
                    }
                ]
            }

    monkeypatch.setattr(search.requests, "get", lambda *args, **kwargs: Response())

    assert search.search_github_repos("agent tooling", 1) == [
        {
            "full_name": "owner/repo",
            "description": "Repo description",
            "url": "https://github.com/owner/repo",
            "stars": 42,
            "language": "Python",
            "updated_at": "2026-03-10T00:00:00Z",
        }
    ]

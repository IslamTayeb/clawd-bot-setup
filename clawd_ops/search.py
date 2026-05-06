import os
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from requests.exceptions import SSLError
from urllib.parse import parse_qs, urlparse
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning


def _clamp_max_results(max_results: int, default: int = 8, limit: int = 20) -> int:
    try:
        value = int(max_results)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, limit))


def _normalize_authors(authors) -> list[str]:
    if isinstance(authors, str):
        return [author.strip() for author in authors.split(" and ") if author.strip()][:5]
    if isinstance(authors, list):
        return [str(author).strip() for author in authors if str(author).strip()][:5]
    return []


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _fetch_text(url: str, verify: bool = True) -> str:
    response = requests.get(
        url,
        timeout=15,
        headers={"User-Agent": "Mozilla/5.0 (compatible; research-bot)"},
        verify=verify,
    )
    response.raise_for_status()
    return _extract_text(response.text)[:8000]


def _duckduckgo_result_url(href: str) -> str:
    parsed = urlparse(href)
    if parsed.path == "/l/":
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return target
    return href


def search_web(query: str, max_results: int = 8) -> list[dict]:
    cleaned_query = " ".join(query.split()).strip()
    if not cleaned_query:
        raise ValueError("query must not be empty")

    limit = _clamp_max_results(max_results)
    response = requests.get(
        "https://duckduckgo.com/html/",
        params={"q": cleaned_query},
        timeout=15,
        headers={"User-Agent": "Mozilla/5.0 (compatible; research-bot)"},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    for result in soup.select(".result"):
        link = result.select_one(".result__a")
        if link is None:
            continue
        title = link.get_text(" ", strip=True)
        href = link.get("href", "")
        snippet_node = result.select_one(".result__snippet")
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
        if not title or not href:
            continue
        results.append(
            {
                "title": title,
                "url": _duckduckgo_result_url(href),
                "snippet": snippet,
            }
        )
        if len(results) >= limit:
            break
    return results


def search_github_repos(query: str, max_results: int = 8) -> list[dict]:
    cleaned_query = " ".join(query.split()).strip()
    if not cleaned_query:
        raise ValueError("query must not be empty")

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "clawd-research-bot",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.get(
        "https://api.github.com/search/repositories",
        params={
            "q": cleaned_query,
            "sort": "stars",
            "order": "desc",
            "per_page": _clamp_max_results(max_results),
        },
        timeout=15,
        headers=headers,
    )
    response.raise_for_status()
    payload = response.json()

    results = []
    for item in payload.get("items", [])[: _clamp_max_results(max_results)]:
        results.append(
            {
                "full_name": item.get("full_name", ""),
                "description": item.get("description") or "",
                "url": item.get("html_url", ""),
                "stars": item.get("stargazers_count", 0),
                "language": item.get("language") or "",
                "updated_at": item.get("updated_at", ""),
            }
        )
    return results


def search_arxiv(query: str, max_results: int = 5) -> list[dict]:
    url = "https://export.arxiv.org/api/query"
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()

    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(response.text)
    results = []
    for entry in root.findall("atom:entry", namespace):
        title = (
            entry.findtext("atom:title", default="", namespaces=namespace)
            .strip()
            .replace("\n", " ")
        )
        summary = (
            entry.findtext("atom:summary", default="", namespaces=namespace)
            .strip()
            .replace("\n", " ")
        )
        authors = [
            author.findtext("atom:name", default="", namespaces=namespace)
            for author in entry.findall("atom:author", namespace)
        ]
        link = entry.findtext("atom:id", default="", namespaces=namespace).strip()
        results.append(
            {
                "title": title,
                "authors": _normalize_authors(authors),
                "abstract": summary[:500],
                "url": link,
            }
        )
    return results


def search_scholar(query: str, max_results: int = 5) -> list[dict]:
    try:
        from scholarly import scholarly

        results = []
        search = scholarly.search_pubs(query)
        for _ in range(max_results):
            try:
                publication = next(search)
                bibliography = publication.get("bib", {})
                results.append(
                    {
                        "title": bibliography.get("title", ""),
                        "authors": _normalize_authors(bibliography.get("author", [])),
                        "abstract": bibliography.get("abstract", "")[:500],
                        "url": publication.get("pub_url", ""),
                        "year": bibliography.get("pub_year", ""),
                    }
                )
            except StopIteration:
                break
        return results
    except Exception as exc:
        return [{"error": str(exc)}]


def search_papers(query: str, max_results: int = 5) -> list[dict]:
    results = []
    try:
        results.extend(search_arxiv(query, max_results))
    except Exception:
        pass
    try:
        results.extend(search_scholar(query, max_results))
    except Exception:
        pass
    return results


def browse_web(url: str) -> str:
    fallback_text = ""

    try:
        fallback_text = _fetch_text(url)
        if len(fallback_text) > 200:
            return fallback_text
    except SSLError:
        disable_warnings(InsecureRequestWarning)
        try:
            fallback_text = _fetch_text(url, verify=False)
            if len(fallback_text) > 200:
                return fallback_text
        except Exception:
            pass
    except Exception:
        pass

    try:
        os.environ.setdefault("PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS", "1")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=20000)
            page.wait_for_load_state("domcontentloaded")
            text = page.inner_text("body")
            browser.close()
            if text.strip():
                return text[:8000]
    except Exception as exc:
        if fallback_text:
            return fallback_text
        return f"Failed to fetch URL: {exc}"

    if fallback_text:
        return fallback_text
    return "No readable text found."

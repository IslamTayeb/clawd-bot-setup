import os
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from requests.exceptions import SSLError
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning


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
    resp = requests.get(
        url,
        timeout=15,
        headers={"User-Agent": "Mozilla/5.0 (compatible; research-bot)"},
        verify=verify,
    )
    resp.raise_for_status()
    return _extract_text(resp.text)[:8000]


def search_arxiv(query: str, max_results: int = 5) -> list[dict]:
    """Search arXiv for papers matching a query."""
    url = "https://export.arxiv.org/api/query"
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(resp.text)
    results = []
    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", default="", namespaces=ns)).strip().replace("\n", " ")
        summary = (entry.findtext("atom:summary", default="", namespaces=ns)).strip().replace("\n", " ")
        authors = [author.findtext("atom:name", default="", namespaces=ns) for author in entry.findall("atom:author", ns)]
        link = (entry.findtext("atom:id", default="", namespaces=ns)).strip()
        results.append({
            "title": title,
            "authors": _normalize_authors(authors),
            "abstract": summary[:500],
            "url": link,
        })
    return results


def search_scholar(query: str, max_results: int = 5) -> list[dict]:
    """Search Google Scholar via scholarly library."""
    try:
        from scholarly import scholarly
        results = []
        search = scholarly.search_pubs(query)
        for _ in range(max_results):
            try:
                pub = next(search)
                bib = pub.get("bib", {})
                results.append({
                    "title": bib.get("title", ""),
                    "authors": _normalize_authors(bib.get("author", [])),
                    "abstract": bib.get("abstract", "")[:500],
                    "url": pub.get("pub_url", ""),
                    "year": bib.get("pub_year", ""),
                })
            except StopIteration:
                break
        return results
    except Exception as e:
        return [{"error": str(e)}]


def search_papers(query: str, max_results: int = 5) -> list[dict]:
    """Search both arXiv and Scholar, return combined results."""
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
    """Fetch and extract text content from a URL. Falls back to Playwright for JS-heavy pages."""
    fallback_text = ""

    # Try simple HTTP fetch first
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

    # Fall back to headless Chromium
    try:
        os.environ.setdefault("PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS", "1")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=20000)
            page.wait_for_load_state("domcontentloaded")
            text = page.inner_text("body")
            browser.close()
            if text.strip():
                return text[:8000]
    except Exception as e:
        if fallback_text:
            return fallback_text
        return f"Failed to fetch URL: {e}"

    if fallback_text:
        return fallback_text
    return "No readable text found."

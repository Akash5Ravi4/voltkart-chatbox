import os

from tavily import TavilyClient

def search_web(query: str, max_results: int = 5) -> list[dict]:
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("TAVILY_API_KEY is not set in the .env file")
    client = TavilyClient(api_key=key)
    resp = client.search(query=query, max_results=max_results, search_depth="basic")
    results = []
    for r in resp.get("results", []):
        results.append(
            {
                "title": r.get("title") or r.get("url") or "Untitled",
                "url": r.get("url") or "",
                "content": (r.get("content") or "").strip(),
            }
        )
    return results
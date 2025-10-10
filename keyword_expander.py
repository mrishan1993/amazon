import requests

def get_related_keywords(base_keyword, marketplace="amazon.in", limit=10):
    """
    Fetch related keywords from Amazon's autocomplete API.
    """
    try:
        print("[keyword_expander] Fetching related keywords for", base_keyword)
        url = f"https://completion.{marketplace}/api/2017/suggestions"
        params = {"limit": limit, "prefix": base_keyword}
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        suggestions = [s["value"] for s in data.get("suggestions", [])]
        return suggestions
    except Exception as e:
        print(f"[keyword_expander] Failed for {base_keyword}: {e}")
        return []

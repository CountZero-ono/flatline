import requests

from flatline_l3_query import gap_search, ensure_collection

SEARXNG_URL = "http://localhost:8080/search"
L3_GAP_THRESHOLD = 3
L3_CONFIDENCE = 0.6
EXTERNAL_CONFIDENCE = 0.4


def get_gap_facts(neo4j_session):
    """Queries Neo4j for all Fact nodes with status 'GAP'."""
    result = neo4j_session.run(
        """
        MATCH (f:Fact)
        WHERE f.status = 'GAP'
        RETURN f.id AS id, f.statement AS statement,
               f.subject AS subject, f.predicate AS predicate, f.object AS object
        """
    )
    return [record.data() for record in result]


def search_searxng(query, num_results=5):
    """POSTs to SearXNG JSON API. Returns list of dicts with title, url, content."""
    try:
        resp = requests.post(
            SEARXNG_URL,
            json={
                "q": query,
                "format": "json",
                "number_of_results": num_results,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = []
        for hit in data.get("results", [])[:num_results]:
            results.append({
                "title": hit.get("title", ""),
                "url": hit.get("url", ""),
                "content": hit.get("content", ""),
            })
        return results
    except Exception:
        return []


def _format_candidates(results, gap_id, confidence):
    """Formats search results as EXTERNAL CANDIDATE fact dicts."""
    candidates = []
    for i, r in enumerate(results):
        statement = r.get("title", "") + " " + r.get("content", "")
        candidates.append({
            "statement": statement.strip(),
            "source_type": "EXTERNAL",
            "status": "CANDIDATE",
            "confidence": confidence,
            "url": r.get("url", ""),
            "gap_id": gap_id,
        })
    return candidates


def handle_gap(neo4j_session, gap_fact):
    """Runs L3 search and external search against a GAP fact, returns candidates."""
    ensure_collection()
    results = gap_search(gap_fact["statement"])
    if len(results) >= L3_GAP_THRESHOLD:
        candidates = _format_candidates(results, gap_fact["id"], L3_CONFIDENCE)
        return {"source": "l3", "candidates": candidates, "gap_id": gap_fact["id"]}
    external = search_searxng(gap_fact["statement"])
    candidates = _format_candidates(external, gap_fact["id"], EXTERNAL_CONFIDENCE)
    return {"source": "searxng", "candidates": candidates, "gap_id": gap_fact["id"]}


def run_gap_handler(neo4j_session):
    """Runs the full gap handling pipeline for all GAP facts."""
    gap_facts = get_gap_facts(neo4j_session)
    return [handle_gap(neo4j_session, gf) for gf in gap_facts]

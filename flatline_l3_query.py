import requests

QDRANT_URL = "http://192.168.1.44:6333"
COLLECTION_NAME = "flatline"
EMBEDDING_URL = "http://192.168.1.112:1236/v1/embeddings"
EMBEDDING_MODEL = "granite-embed-97m"
TOP_K = 5


def embed(text):
    """Sends text to Granite embedding endpoint, returns the embedding vector."""
    payload = {
        "model": EMBEDDING_MODEL,
        "input": [text],
    }
    resp = requests.post(EMBEDDING_URL, json=payload)
    if resp.status_code != 200:
        raise RuntimeError(f"Embedding request failed: {resp.status_code} {resp.text}")
    data = resp.json()
    try:
        return data["data"][0]["embedding"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Unexpected embedding response: {data}")


def ensure_collection():
    """Checks if the flatline collection exists in Qdrant. Creates it if missing."""
    resp = requests.get(f"{QDRANT_URL}/collections")
    if resp.status_code != 200:
        raise RuntimeError(f"Qdrant list-collections failed: {resp.status_code} {resp.text}")
    existing = resp.json().get("result", {}).get("collections", [])
    names = [c["name"] for c in existing]
    if COLLECTION_NAME in names:
        return
    create_payload = {
        "vectors": {
            "size": 384,
            "distance": "Cosine",
        },
    }
    resp = requests.put(
        f"{QDRANT_URL}/collections/{COLLECTION_NAME}",
        json=create_payload,
    )
    if resp.status_code not in (200, 201, 409):
        raise RuntimeError(f"Qdrant create-collection failed: {resp.status_code} {resp.text}")


def search(query_text, top_k=TOP_K, filter_payload=None, collection=None):
    """Searches the flatline collection for the most similar points."""
    if isinstance(query_text, list):
        vector = query_text
    else:
        vector = embed(query_text)
    payload = {
        "vector": vector,
        "limit": top_k,
        "with_payload": True,
    }
    if filter_payload:
        payload["filter"] = filter_payload
    resp = requests.post(
        f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/search",
        json=payload,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Qdrant search failed: {resp.status_code} {resp.text}")
    data = resp.json()
    results = []
    for hit in data.get("result", []):
        point = hit.get("payload", {})
        entry = {
            "score": hit.get("score"),
            "content": point.get("content"),
            "source_name": point.get("source_name"),
            "source_type": point.get("source_type"),
        }
        for key, val in point.items():
            if key not in entry:
                entry[key] = val
        results.append(entry)
    return results


def gap_search(observation_content, top_k=TOP_K):
    """Convenience wrapper — searches using observation content as the query."""
    return search(observation_content, top_k=top_k)


def upsert_chunk(chunk_id, content, metadata):
    """Embeds content and upserts a single point to the flatline collection."""
    if not isinstance(chunk_id, int):
        raise ValueError("chunk_id must be an integer")
    vector = embed(content)
    payload = {
        "collection_name": COLLECTION_NAME,
        "operations": [
            {
                "type": "upsert",
                "upsert": {
                    "points": [
                        {
                            "id": chunk_id,
                            "vector": vector,
                            "payload": {**metadata, "content": content},
                        }
                    ]
                }
            }
        ]
    }
    resp = requests.post(
        f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/batch",
        json=payload,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Qdrant upsert failed: {resp.status_code} {resp.text}")

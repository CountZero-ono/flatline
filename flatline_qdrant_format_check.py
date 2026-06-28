"""
flatline_qdrant_format_check.py — settles the open question from the
kb_ingest audit: does the existing /points/batch payload shape (used in
both flatline_l3_query.py::upsert_chunk and flatline_kb_ingest.py) actually
work against the real Qdrant instance, or does it only happen to work by
accident?

Run this ON THE NETWORK THAT CAN REACH QDRANT (192.168.1.44) — it won't
run from a sandboxed environment without LAN access.

Writes one throwaway test point, tagged so it's unmistakable, then deletes
it. Does not touch any real KnowledgeNode/Fact data.

    python3 flatline_qdrant_format_check.py
"""

import requests

QDRANT_URL = "http://192.168.1.44:6333"
COLLECTION_NAME = "flatline"
TEST_POINT_ID = 999999999  # well outside any real md5-derived id range
TEST_VECTOR = [0.001] * 384  # matches the 384-dim Granite embedding size


def try_existing_shape():
    """The shape currently used in upsert_chunk() and kb_ingest.py:
    extra 'type' field per operation, 'collection_name' inside the body.
    """
    payload = {
        "collection_name": COLLECTION_NAME,
        "operations": [
            {
                "type": "upsert",
                "upsert": {
                    "points": [
                        {
                            "id": TEST_POINT_ID,
                            "vector": TEST_VECTOR,
                            "payload": {"node_type": "FORMAT_CHECK_DELETE_ME"},
                        }
                    ]
                },
            }
        ],
    }
    resp = requests.post(
        f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/batch",
        json=payload,
        timeout=15,
    )
    return resp


def try_documented_shape():
    """The shape per Qdrant's own API docs: no 'type' field, no
    'collection_name' in the body (collection_name is a path param).
    """
    payload = {
        "operations": [
            {
                "upsert": {
                    "points": [
                        {
                            "id": TEST_POINT_ID,
                            "vector": TEST_VECTOR,
                            "payload": {"node_type": "FORMAT_CHECK_DELETE_ME"},
                        }
                    ]
                }
            }
        ]
    }
    resp = requests.post(
        f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/batch",
        json=payload,
        timeout=15,
    )
    return resp


def cleanup():
    """Delete the test point regardless of which attempt(s) succeeded."""
    resp = requests.post(
        f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/delete",
        json={"points": [TEST_POINT_ID]},
        timeout=15,
    )
    return resp


if __name__ == "__main__":
    print("Testing EXISTING shape (type field + collection_name in body)...")
    r1 = try_existing_shape()
    print(f"  -> HTTP {r1.status_code}: {r1.text[:300]}")

    print("\nTesting DOCUMENTED shape (per Qdrant API reference)...")
    r2 = try_documented_shape()
    print(f"  -> HTTP {r2.status_code}: {r2.text[:300]}")

    print("\nCleaning up test point...")
    r3 = cleanup()
    print(f"  -> HTTP {r3.status_code}")

    print("\n--- Verdict ---")
    if r1.status_code == 200 and r2.status_code == 200:
        print("Both shapes work. The existing pattern in upsert_chunk() / "
              "kb_ingest.py is safe as-is — Qdrant ignores the extra fields.")
    elif r1.status_code == 200 and r2.status_code != 200:
        print("Only the EXISTING shape works (surprising — worth a second look "
              "at exact Qdrant server version behavior).")
    elif r1.status_code != 200 and r2.status_code == 200:
        print("CONFIRMED: the existing shape is broken. It should be writing "
              "non-200 responses, which means write_chunk()/upsert_knowledge_node_vector() "
              "calls are silently failing in production right now. Fix flatline_l3_query.py "
              "and flatline_kb_ingest.py to drop the 'type' field and 'collection_name' "
              "from the request body.")
    else:
        print("Neither shape worked — check Qdrant is reachable and the "
              "'flatline' collection exists before re-running.")

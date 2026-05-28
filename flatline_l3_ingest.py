import hashlib
import json
from pathlib import Path

from flatline_l3_query import ensure_collection, upsert_chunk

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Splits text into overlapping word-based chunks."""
    words = text.split()
    if not words:
        return []
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start = end - overlap
    return chunks


def stable_id(source_name, chunk_index):
    """Generates a stable integer ID for a chunk."""
    key = f"{source_name}:{chunk_index}"
    hex_str = hashlib.md5(key.encode()).hexdigest()[:8]
    return int(hex_str, 16)


def ingest_text(text, source_name, source_type="document", extra_metadata=None):
    """Ingests text into the flatline collection, chunked and embedded."""
    ensure_collection()
    chunks = chunk_text(text)
    total = len(chunks)
    metadata_template = {
        "source_name": source_name,
        "source_type": source_type,
        "total_chunks": total,
    }
    if extra_metadata:
        metadata_template.update(extra_metadata)
    for i, chunk in enumerate(chunks):
        meta = {**metadata_template, "chunk_index": i}
        cid = stable_id(source_name, i)
        upsert_chunk(cid, chunk, meta)
    return {"ingested": total, "source_name": source_name, "total_chunks": total}


def ingest_file(path, source_type="document", extra_metadata=None):
    """Reads a plain text file and ingests it into the flatline collection."""
    p = Path(path)
    if not p.exists():
        raise ValueError(f"File not found: {path}")
    if p.suffix.lower() != ".txt":
        raise ValueError(f"Only .txt files are supported (got {p.suffix})")
    text = p.read_text(encoding="utf-8")
    return ingest_text(text, source_name=p.stem, source_type=source_type, extra_metadata=extra_metadata)

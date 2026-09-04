#!/usr/bin/env python3
"""
EKA Agent — ChromaDB Vector Database Setup
Creates a vector database from training chunks for semantic search.

Usage:
  python eka_vector_db.py --setup          # Initialize DB + index instruction chunks (first 10)
  python eka_vector_db.py --index-all      # Index ALL chunks (takes hours for 1M+ chunks)
  python eka_vector_db.py --index-batch N  # Index N chunks starting from offset
  python eka_vector_db.py --index-new      # Index only chunks added since last index
  python eka_vector_db.py --search "query" # Search the vector DB
  python eka_vector_db.py --stats          # Show DB stats
"""
import os
import sys
import json
import hashlib
import argparse
from datetime import datetime, timezone

import chromadb

# ─── Config ───
TRAINING_FILE = r"D:\training-data\agent_training_chunks_with_learning.jsonl"
VECTOR_DB_DIR = r"D:\training-data\vector_db"
COLLECTION_NAME = "eka_agent_knowledge"
STATE_DIR = os.path.join(os.path.expanduser("~"), ".eka_agent")
INDEX_STATE_FILE = os.path.join(STATE_DIR, "vector_index_state.json")

def get_state():
    """Get current index state."""
    try:
        with open(INDEX_STATE_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"last_indexed_line": 0, "total_indexed": 0}

def save_state(state):
    """Save index state."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(INDEX_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def get_client():
    """Get ChromaDB client."""
    return chromadb.PersistentClient(path=VECTOR_DB_DIR)

def get_collection():
    """Get or create the main collection."""
    client = get_client()
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "EKA Agent knowledge base — all training chunks"}
    )

def chunk_to_document(chunk):
    """Convert a training chunk to a searchable document string."""
    parts = []
    parts.append(f"Title: {chunk.get('title', '')}")
    parts.append(f"Category: {chunk.get('category', '')}")
    parts.append(f"Input: {chunk.get('input', '')[:500]}")
    output = chunk.get('output', '')
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
            parts.append(f"Output: {json.dumps(parsed, indent=2, default=str)[:1000]}")
        except json.JSONDecodeError:
            parts.append(f"Output: {output[:1000]}")
    else:
        parts.append(f"Output: {str(output)[:1000]}")
    return "\n\n".join(parts)

def chunk_to_metadata(chunk):
    """Extract metadata for ChromaDB (must be simple types)."""
    meta = chunk.get("metadata", {})
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}

    return {
        "chunk_id": str(chunk.get("id", "")),
        "title": str(chunk.get("title", ""))[:200],
        "category": str(chunk.get("category", "")),
        "source_file": str(chunk.get("source_file", ""))[:200],
        "agent_visibility": str(meta.get("agent_visibility", "")),
        "priority": str(meta.get("priority", "")),
        "source_device": str(meta.get("source_device", "")),
        "daily_batch": str(meta.get("daily_batch", "")),
    }

def index_chunks(start_line=0, count=None, batch_size=50):
    """Index chunks from the training file into ChromaDB."""
    collection = get_collection()
    state = get_state()

    if start_line == 0 and state["last_indexed_line"] > 0:
        start_line = state["last_indexed_line"]
        print(f"  Resuming from line {start_line}")

    print(f"  Indexing from line {start_line}...")
    if count:
        print(f"  Will index {count} chunks max.")

    indexed = 0
    current_line = 0
    batch_docs = []
    batch_ids = []
    batch_meta = []

    with open(TRAINING_FILE, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                current_line += 1
                continue
            if current_line < start_line:
                current_line += 1
                continue

            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                current_line += 1
                continue

            doc = chunk_to_document(chunk)
            chunk_id = f"chunk_{current_line}_{hashlib.sha256(doc.encode()).hexdigest()[:8]}"
            meta = chunk_to_metadata(chunk)

            batch_docs.append(doc)
            batch_ids.append(chunk_id)
            batch_meta.append(meta)
            indexed += 1
            current_line += 1

            if len(batch_docs) >= batch_size:
                collection.add(
                    documents=batch_docs,
                    ids=batch_ids,
                    metadatas=batch_meta
                )
                print(f"  Indexed {indexed:,} chunks (line {current_line:,})...")
                state["last_indexed_line"] = current_line
                state["total_indexed"] = state["total_indexed"] + len(batch_docs)
                save_state(state)
                batch_docs = []
                batch_ids = []
                batch_meta = []

            if count and indexed >= count:
                break

    # Flush remaining
    if batch_docs:
        collection.add(
            documents=batch_docs,
            ids=batch_ids,
            metadatas=batch_meta
        )
        state["last_indexed_line"] = current_line
        state["total_indexed"] = state["total_indexed"] + len(batch_docs)
        save_state(state)

    print(f"  Total indexed this run: {indexed:,}")
    print(f"  Total in DB: {state['total_indexed']:,}")
    print(f"  Last indexed line: {current_line:,}")

    return {
        "indexed_this_run": indexed,
        "total_in_db": state['total_indexed'],
        "last_indexed_line": current_line,
    }

def search(query, n_results=10, json_mode=False):
    """Search the vector DB."""
    collection = get_collection()
    results = collection.query(
        query_texts=[query],
        n_results=n_results
    )

    items = []
    for i, (doc_id, doc, meta, dist) in enumerate(zip(
        results["ids"][0],
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0]
    )):
        items.append({
            "rank": i + 1,
            "category": meta.get('category','?'),
            "title": meta.get('title','?')[:80],
            "distance": round(dist, 4),
            "chunk_id": meta.get('chunk_id','?'),
            "priority": meta.get('priority','?'),
            "device": meta.get('source_device','?'),
            "preview": doc[:200],
        })

    if json_mode:
        print(json.dumps({"query": query, "results": items}, ensure_ascii=False))
    else:
        print(f"\n=== Search Results for: '{query}' ===")
        print(f"  Found {len(results['ids'][0])} results:\n")
        for item in items:
            print(f"  {item['rank']}. [{item['category']}] {item['title']}")
            print(f"     Distance: {item['distance']}")
            print(f"     ID: {item['chunk_id']}")
            print(f"     Priority: {item['priority']}")
            print(f"     Device: {item['device']}")
            print(f"     Preview: {item['preview']}...")
            print()

def stats(json_mode=False):
    """Show vector DB stats."""
    collection = get_collection()
    count = collection.count()
    state = get_state()

    with open(TRAINING_FILE, "r", encoding="utf-8", errors="replace") as f:
        total_lines = sum(1 for _ in f)
    data = {
        "collection": COLLECTION_NAME,
        "location": VECTOR_DB_DIR,
        "documents_indexed": count,
        "last_indexed_line": state.get('last_indexed_line', 0),
        "total_indexed_all_time": state.get('total_indexed', 0),
        "training_file_lines": total_lines,
        "coverage_percent": round((count / total_lines * 100), 1) if total_lines else 0,
    }
    if json_mode:
        print(json.dumps(data, ensure_ascii=False))
    else:
        print(f"\n=== Vector DB Stats ===")
        print(f"  Collection: {COLLECTION_NAME}")
        print(f"  Location: {VECTOR_DB_DIR}")
        print(f"  Documents indexed: {count:,}")
        print(f"  Last indexed line: {state.get('last_indexed_line', 0):,}")
        print(f"  Total indexed (all time): {state.get('total_indexed', 0):,}")
        print(f"  Training file lines: {total_lines:,}")
        print(f"  Coverage: {(count / total_lines * 100):.1f}%")
    return data

# ─── MAIN ───
def main():
    parser = argparse.ArgumentParser(description="EKA Agent — Vector DB Management")
    parser.add_argument("--setup", action="store_true", help="Initialize DB + index first 10 instruction chunks")
    parser.add_argument("--index-all", action="store_true", help="Index ALL chunks (slow for 1M+)")
    parser.add_argument("--index-batch", type=int, metavar="N", help="Index N chunks from last position")
    parser.add_argument("--index-new", action="store_true", help="Index only new chunks since last index")
    parser.add_argument("--search", metavar="QUERY", help="Search the vector DB")
    parser.add_argument("--stats", action="store_true", help="Show DB stats")
    parser.add_argument("--json", action="store_true", help="Emit result as JSON")
    args = parser.parse_args()

    if args.setup:
        print("=== Setting up ChromaDB ===")
        os.makedirs(VECTOR_DB_DIR, exist_ok=True)
        print(f"  DB location: {VECTOR_DB_DIR}")
        print("  Indexing first 10 instruction chunks...")
        index_chunks(start_line=0, count=10)

    elif args.index_all:
        print("=== Indexing ALL chunks ===")
        print("  WARNING: This will take a long time for 1M+ chunks.")
        index_chunks(batch_size=50)

    elif args.index_batch is not None:
        print(f"=== Indexing batch of {args.index_batch} chunks ===")
        index_chunks(count=args.index_batch, batch_size=50)

    elif args.index_new:
        print("=== Indexing new chunks ===")
        state = get_state()
        print(f"  Resuming from line {state['last_indexed_line']:,}")
        index_chunks(batch_size=50)

    elif args.search:
        search(args.search, json_mode=args.json)

    elif args.stats:
        stats(json_mode=args.json)

    else:
        parser.print_help()

if __name__ == "__main__":
    main()
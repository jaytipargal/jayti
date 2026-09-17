import json, os, time, numpy as np, faiss
from sentence_transformers import SentenceTransformer
IDX="/opt/eka-agent/faiss_index"
docs=[]
with open(f"{IDX}/doc_store.jsonl",encoding="utf-8") as f:
    for line in f:
        docs.append(json.loads(line)["document"])
print("docs", len(docs), flush=True)
m=SentenceTransformer("all-MiniLM-L6-v2")
emb=m.encode(docs, batch_size=64, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False).astype("float32")
print("emb", emb.shape, flush=True)
index=faiss.IndexFlatIP(int(emb.shape[1])); index.add(emb)
faiss.write_index(index, f"{IDX}/faiss_index.bin")
try: os.remove(f"{IDX}/doc_offsets.bin")
except FileNotFoundError: pass
json.dump({"type":"IndexFlatIP","dim":int(emb.shape[1]),"model":"all-MiniLM-L6-v2","count":len(docs),"source":"go4garage_automotive","built":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}, open(f"{IDX}/index_meta.json","w"))
print("wrote", index.ntotal, "vectors", flush=True)

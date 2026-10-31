"""Fresh process: open the library, run recalls, import one more session (an append save); report RSS."""
import os, resource, subprocess, sys, time
os.environ["ENGRAM_NO_DOTENV"] = "1"
from engram.service import MemoryService

def rss_mb():
    return int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())]).strip()) / 1024

data = sys.argv[1]
base = rss_mb()
t = time.time()
svc = MemoryService(data_dir=data, embedder_name="hashing", llm_name="")
mem = svc.get("me")
open_s = time.time() - t
after_open = rss_mb()
t = time.time()
for q in ["合同 报价", "上次复盘的结论", "回滚 部署 风险", "engram recall"]:
    ctx = svc.recall("me", q, answer=False)["context"]
recall_s = (time.time() - t) / 4
after_recall = rss_mb()
store = svc._path("me")
size_before = os.path.getsize(os.path.join(store, "episodes.jsonl"))
t = time.time()
svc.import_("me", sessions=[{"session_id": "codex:new", "metadata": {"source": "agent_session"},
                             "messages": [{"role": "user", "content": "新会话：合同条款下周三复盘 " * 800}]}])
save_s = time.time() - t
size_after = os.path.getsize(os.path.join(store, "episodes.jsonl"))
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
print(f"python baseline {base:.0f}MB | after open {after_open:.0f}MB ({open_s:.1f}s) | after recall {after_recall:.0f}MB "
      f"({recall_s*1000:.0f}ms/query) | import+save {save_s:.2f}s, episodes.jsonl +{(size_after-size_before)/1024:.0f}KB | peak {peak:.0f}MB")
print("context sample:", ctx[:80].replace("\n", " "))

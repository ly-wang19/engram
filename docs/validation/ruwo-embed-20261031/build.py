"""Build a synthetic single-user Engram library at the owner's real scale (2280 Codex sessions)."""
import os, random, sys, time
from engram.service import MemoryService
from engram.types import Fact

data = sys.argv[1]
random.seed(7)
words_zh = "报价 合同 客户 复盘 会议 项目 排期 交付 风险 预算 方案 评审 上线 回滚 测试 部署 钉钉 记忆 提炼 结论 决定 教训 问题 进展 周报 审批 采购 招聘 绩效 数据 模型 接口 前端 后端 数据库 权限 安全 日志 监控 告警".split()
words_en = "engram codex deploy release review merge branch commit pytest frontend backend schema index recall".split()

def text(n):
    out, size = [], 0
    while size < n:
        w = random.choice(words_zh) if random.random() < 0.7 else random.choice(words_en)
        out.append(w); size += len(w) + 1
    return " ".join(out)[:n]

os.environ["ENGRAM_NO_DOTENV"] = "1"
svc = MemoryService(data_dir=data, embedder_name="hashing", llm_name="")
mem = svc.get("me")
t = time.time()
sessions = []
for i in range(2280):
    body = text(24000)
    if i % 10 == 0:
        body = body[:-2] + "✅"  # some transcripts carry an emoji, which makes Python store them 4 bytes/char
    sessions.append({"session_id": f"codex:2026-{i:05d}", "metadata": {"source": "agent_session"},
                     "messages": [{"role": "user", "content": body}]})
    if len(sessions) == 200:
        mem.import_messages(sessions, user_id="me", summarize=False); sessions = []
mem.import_messages(sessions, user_id="me", summarize=False)
emb = mem.embedder
for ep in mem.episodes_doc.values():
    ep.summary = text(2000)
    ep.summary_embedding = emb.embed(ep.summary)
    mem.summary_vec.upsert(ep.id, ep.summary_embedding, ep)
for j in range(10000):
    statement = text(100)
    f = Fact(subject=f"codex:2026-{j % 2280:05d}", predicate=random.choice(["decision", "finding", "lesson", "open_question"]),
             object=statement, text=statement, display=statement, user_id="me", category="outcomes")
    f.embedding = emb.embed(f.text)
    mem.fact_store.upsert(f.id, f.embedding, f)
mem.save()
print(f"built {len(mem.episodes_doc.values())} episodes, {len(mem.fact_store.values())} facts in {time.time()-t:.0f}s")

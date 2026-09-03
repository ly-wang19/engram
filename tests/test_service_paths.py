from __future__ import annotations

import multiprocessing
import os
import queue
import shutil
from pathlib import Path

from engram import Memory
from engram.embed import HashingEmbedder
from engram.llm.providers import make_embedder
from engram.service import MemoryService
from engram.types import Entity, Relation


def _remember_in_process(data_dir: str, user: str, session_id: str, content: str, out) -> None:
    try:
        svc = MemoryService(data_dir=data_dir, embedder_name="hashing", llm_name="")
        result = svc.remember(user, content, session_id=session_id, scope="long")
        out.put({"ok": result.get("ok"), "extracted": result.get("extracted", 0)})
    except BaseException as exc:  # noqa: BLE001 - surfaced in the parent test process.
        out.put({"error": repr(exc)})


def test_make_embedder_defaults_to_offline_hashing():
    assert isinstance(make_embedder(), HashingEmbedder)


def test_service_defaults_to_offline_hashing_embedder(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_EMBEDDER", raising=False)
    svc = MemoryService(data_dir=str(tmp_path))
    assert isinstance(svc.embedder, HashingEmbedder)


def test_service_reads_max_hot_facts_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_MAX_HOT_FACTS", "3")
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    assert svc.config.max_hot_facts == 3


def test_service_uses_directory_namespace_path(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    path = svc._path("alice@example.com")
    assert Path(path).parent == tmp_path
    assert Path(path).name.startswith("alice-example-com--")
    assert not path.endswith(".pkl")


def test_namespace_paths_are_unique_deterministic_and_contained(tmp_path):
    data = tmp_path / "data"
    svc = MemoryService(data_dir=str(data), embedder_name="hashing")
    users = (
        "a/b",
        "ab",
        ".",
        "..",
        "../outside",
        str(tmp_path / "absolute"),
        "中文租户",
        "e\u0301",
        "é",
        "x" * 1024,
    )

    first = [Path(svc._path(user)) for user in users]
    second = [Path(svc._path(user)) for user in users]

    assert first == second
    assert len(set(first)) == len(users)
    assert all(path.parent == data for path in first)
    assert all(path.name not in {"", ".", ".."} for path in first)
    assert Path(svc._path("a/b")) != Path(svc._path("ab"))
    assert Path(svc._path("e\u0301")) != Path(svc._path("é"))


def test_forget_never_targets_data_root_or_parent(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    svc = MemoryService(data_dir=str(data), embedder_name="hashing")
    removed: list[Path] = []

    monkeypatch.setattr(shutil, "rmtree", lambda path: removed.append(Path(path).resolve()))
    monkeypatch.setattr(os, "remove", lambda path: removed.append(Path(path).resolve()))

    assert svc.forget("..") == {"ok": True, "message": "all memory for '..' erased"}
    root = data.resolve()
    assert removed
    assert all(path != root and root in path.parents for path in removed)


def test_service_loads_legacy_pkl_named_store_and_forget_removes_both(tmp_path):
    legacy = tmp_path / "alice.pkl"
    mem = Memory()
    mem.add("I live in Shenzhen.", user_id="alice")
    mem.save(str(legacy))

    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    loaded = svc.get("alice")
    assert loaded.episodes_doc.values()[0].content == "I live in Shenzhen."

    new_path = tmp_path / "alice"
    new_path.mkdir()
    assert legacy.exists() and new_path.exists()
    assert svc.forget("alice")["ok"] is True
    assert not legacy.exists()
    assert not new_path.exists()


def test_service_stats_reports_consolidation_backlog_without_content(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    mem = svc.get("u")
    mem.add("I live in Shenzhen.", user_id="u")
    mem.remember("Today my throat hurts.", user_id="u", scope="working")

    before = svc.stats("u")
    assert before["counts"]["episodes"] == 2
    assert before["counts"]["episodes_pending"] == 1
    assert before["counts"]["episodes_consolidated"] == 1
    assert before["counts"]["episodes_ephemeral"] == 1
    assert before["counts"]["working_live"] == 1
    assert before["consolidation_backlog"] is True
    assert "Shenzhen" not in str(before)
    assert "throat" not in str(before).lower()

    mem.consolidate()
    after = svc.stats("u")
    assert after["counts"]["episodes_pending"] == 0
    assert after["counts"]["episodes_consolidated"] == 2
    assert after["consolidation_backlog"] is False


def test_service_close_session_grooms_one_session_and_clears_working(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    mem = svc.get("u")
    mem.add("I work at Acme.", user_id="u", session_id="s1")
    svc.add_working("u", "today I am focused on testing", session_id="s1")

    out = svc.close_session("u", "s1")

    assert out["ok"] is True
    assert out["session_id"] == "s1"
    assert out["episodes"] == 1
    assert out["pending_consolidated"] == 1
    assert out["facts_added"] >= 1
    assert out["summaries"] == 1
    assert out["working_cleared"] == 1
    assert svc.working_memory("u", session_id="s1")["items"] == []

    stats = svc.stats("u")
    assert stats["counts"]["episodes_pending"] == 0
    dump = svc.memories("u", include_sensitive=True)
    assert dump["counts"]["summaries"] == 1
    assert dump["episodes"][0]["summary"]

    reloaded = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    assert reloaded.memories("u")["counts"]["summaries"] == 1


def test_service_handoffs_memory_across_agent_sessions(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    user = "shared-user"
    codex_session = "codex:super-memory:thread-a"
    claude_session = "claude-code:super-memory:thread-b"

    written = svc.remember(
        user,
        "Project decision: the launch checklist must include committed eval logs.",
        session_id=codex_session,
        scope="long",
    )
    assert written["ok"] is True
    assert written["extracted"] >= 1
    assert svc.close_session(user, codex_session)["ok"] is True

    recalled = svc.recall(
        user,
        "What launch checklist decision did Codex record?",
        session_id=claude_session,
        n_chunks=3,
    )

    assert "committed eval logs" in recalled["context"]
    assert codex_session in recalled["context"]
    assert svc.working_memory(user, session_id=claude_session)["items"] == []

    reloaded = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    after_reload = reloaded.recall(
        user,
        "What should the launch checklist include?",
        session_id=claude_session,
        n_chunks=3,
    )
    assert "committed eval logs" in after_reload["context"]


def test_service_refreshes_cached_namespace_after_external_process_write(tmp_path):
    codex = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    claude = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    user = "shared-user"
    codex_session = "codex:super-memory:external-writer"
    claude_session = "claude-code:super-memory:stale-reader"

    # Claude Code has already opened and cached this namespace before Codex writes anything.
    before = claude.agent_status(user, session_id=claude_session)
    assert before["session"]["episodes"] == 0

    written = codex.remember(
        user,
        "Project decision: local MCP agents must refresh disk snapshots before recall.",
        session_id=codex_session,
        scope="long",
    )
    assert written["ok"] is True
    assert codex.close_session(user, codex_session)["ok"] is True

    recalled = claude.recall(
        user,
        "What did Codex decide about local MCP agents?",
        session_id=claude_session,
        n_chunks=3,
    )

    assert "refresh disk snapshots" in recalled["context"]
    assert codex_session in recalled["context"]


def test_service_write_lock_preserves_external_process_writes(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    child_result = ctx.Queue()
    parent = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    user = "shared-user"
    parent_session = "codex:super-memory:concurrent-parent"
    child_session = "claude-code:super-memory:concurrent-child"

    with parent.write_lock(user):
        child = ctx.Process(
            target=_remember_in_process,
            args=(
                str(tmp_path),
                user,
                child_session,
                "Project decision: child-write-beta must survive concurrent local MCP writes.",
                child_result,
            ),
        )
        child.start()
        try:
            early = child_result.get(timeout=0.3)
        except queue.Empty:
            early = None
        assert early is None, "child write finished while the parent still held the service write lock"

        mem = parent.get(user)
        parent_fact = mem.add_fact(
            "project",
            "concurrent_parent_write",
            "parent-write-alpha must survive concurrent local MCP writes",
            user_id=user,
        )
        mem.remember_working("parent temporary state", user_id=user, session_id=parent_session)
        assert parent_fact.object.startswith("parent-write-alpha")
        parent._save(user, mem)

    child_msg = child_result.get(timeout=45)
    child.join(timeout=5)
    assert child.exitcode == 0
    assert child_msg == {"ok": True, "extracted": 1}

    reloaded = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    rendered = str(reloaded.memories(user, include_sensitive=True))
    assert "parent-write-alpha" in rendered
    assert "child-write-beta" in rendered
    assert reloaded.working_memory(user, session_id=parent_session)["items"][0]["content"] == "parent temporary state"


def test_service_agent_status_is_content_free_and_session_aware(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    user = "status-user"
    session = "codex:super-memory:thread"
    svc.remember(
        user,
        "My private diagnosis is diabetes and I work at Acme.",
        session_id=session,
        scope="long",
    )
    svc.add_working(user, "today I am preparing a private launch note", session_id=session)
    svc.set_focus(user, track=["project decisions"], mute=["health details"])

    status = svc.agent_status(user, session_id=session)

    assert status["ok"] is True
    assert status["user"] == user
    assert status["session_id"] == session
    assert status["session"]["episodes"] == 1
    assert status["session"]["working_live"] == 1
    assert status["focus"] == {"track": ["project decisions"], "mute": ["health details"]}
    assert status["counts"]["facts_live"] >= 1
    assert "engram_recall" in status["tools"]["read_context"]
    assert any("engram_close_session" in item for item in status["recommended_next_actions"])

    rendered = str(status).lower()
    assert "diabetes" not in rendered
    assert "acme" not in rendered
    assert "private launch note" not in rendered


def test_service_session_report_audits_saved_facts_with_sensitive_redaction(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    user = "report-user"
    session = "codex:super-memory:thread"
    svc.remember(
        user,
        "Project decision: the launch checklist must include committed eval logs.",
        session_id=session,
        scope="long",
    )
    svc.add_fact(user, "user", "has_disease", "diabetes", sensitive=True)
    sensitive = svc.get(user).fact_store.values()[-1]
    sensitive.provenance.append(svc.get(user).episodes_doc.values()[0].id)
    svc.get(user).save()

    report = svc.session_report(user, session)

    assert report["ok"] is True
    assert report["session_id"] == session
    assert report["episodes"] == 1
    assert report["facts_added"] >= 2
    assert report["facts_redacted"] == 1
    rendered = str(report).lower()
    assert "committed eval logs" in rendered
    assert "diabetes" not in rendered
    assert "[redacted sensitive fact]" in rendered

    full = svc.session_report(user, session, include_sensitive=True)
    assert full["facts_redacted"] == 0
    assert "diabetes" in str(full).lower()


def test_service_stats_reports_graph_hygiene_without_content(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    mem = svc.get("u")
    mem.add_fact("user", "works_at", "Acme", user_id="u")
    orphan = mem.graph.upsert_entity(Entity(name="Private Orphan", user_id="u"))
    subject = mem.graph.upsert_entity(Entity(name="ghost subject", user_id="u"))
    obj = mem.graph.upsert_entity(Entity(name="ghost object", user_id="u"))
    mem.graph.add_relation(Relation(subject_id=subject.id, predicate="ghost", object_id=obj.id, fact_id="missing_fact"))

    stats = svc.stats("u")

    assert stats["counts"]["graph_orphan_entities"] == 1
    assert stats["counts"]["graph_stale_relations"] == 1
    rendered = str(stats).lower()
    assert "private orphan" not in rendered
    assert "ghost subject" not in rendered
    assert "ghost object" not in rendered


def test_service_stats_counts_hot_and_cold_facts(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    mem = svc.get("u")
    for i in range(5):
        f = mem.add_fact("project", f"note_{i}", f"value-{i}", user_id="u")
        f.salience = float(i)

    assert mem.evict_cold(max_hot=2) == 3
    stats = svc.stats("u")
    assert stats["counts"]["facts_hot"] == 2
    assert stats["counts"]["facts_cold"] == 3
    assert stats["counts"]["cold_pages_out"] == 3
    assert stats["counts"]["cold_pages_in"] == 0
    assert stats["counts"]["facts_live"] == 5
    assert stats["counts"]["facts_superseded"] == 0
    assert "value-" not in str(stats)

    assert mem.evict_cold(max_hot=0) == 2
    mem.search("value-0", user_id="u")
    stats = svc.stats("u")
    assert stats["counts"]["cold_pages_in"] >= 1


def test_service_memories_and_export_include_cold_facts(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    mem = svc.get("u")
    cold = mem.add_fact("project", "project_note", "cold-value", user_id="u")
    hot = mem.add_fact("project", "project_note_2", "hot-value", user_id="u")
    cold.salience = 0.0
    hot.salience = 1.0
    assert mem.evict_cold(max_hot=1) == 1
    assert mem.cold_store.get(cold.id) is not None

    memories = svc.memories("u")
    assert memories["counts"]["facts_live"] == 2
    assert {f["object"] for f in memories["facts"]} == {"cold-value", "hot-value"}

    exported = svc.export("u")
    assert exported["include_sensitive"] is False
    assert {f["object"] for f in exported["facts"]} == {"cold-value", "hot-value"}


def test_service_fact_edit_delete_persists_clean_graph_files(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    deleted = svc.add_fact("u", "user", "has_disease", "diabetes")["id"]
    edited = svc.add_fact("u", "user", "works_at", "ByteDance")["id"]

    assert svc.update_fact("u", edited, object="Moonshot AI")["ok"] is True
    assert svc.delete_fact("u", deleted)["ok"] is True

    store = Path(svc._path("u"))
    entities_jsonl = (store / "entities.jsonl").read_text(encoding="utf-8")
    relations_jsonl = (store / "relations.jsonl").read_text(encoding="utf-8")
    assert "diabetes" not in entities_jsonl.lower()
    assert "ByteDance" not in entities_jsonl
    assert deleted not in relations_jsonl

    reloaded = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    graph = reloaded.graph("u")
    rendered = str(graph)
    assert "Moonshot AI" in rendered
    assert "ByteDance" not in rendered
    assert "diabetes" not in rendered.lower()
    assert len([edge for edge in graph["edges"] if edge["fact_id"] == edited]) == 1


def test_service_profile_fact_list_includes_cold_facts(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    mem = svc.get("u")
    cold = mem.add_fact("project", "project_note", "cold-profile-value", user_id="u")
    hot = mem.add_fact("project", "project_note_2", "hot-profile-value", user_id="u")
    cold.salience = 0.0
    hot.salience = 1.0
    assert mem.evict_cold(max_hot=1) == 1

    facts = svc.profile("u")["facts"]
    assert any("cold-profile-value" in f for f in facts)
    assert any("hot-profile-value" in f for f in facts)


def test_service_remember_total_facts_counts_cold_tier(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    mem = svc.get("u")
    for i in range(5):
        f = mem.add_fact("project", f"project_note_{i}", f"value-{i}", user_id="u")
        f.salience = float(i)
    assert mem.evict_cold(max_hot=1) == 4

    out = svc.remember("u", "Just saying hello.", scope="long")
    assert out["ok"] is True
    assert out["total_facts"] >= 5


# ---------------------------------------------------------------------------------------------------
# embedder_blind: the store-level audit finding for "recall returns the same four facts for every query".
# Measured on the owner's store: HashingEmbedder scored 0.000 for every Chinese query/fact pair.
# ---------------------------------------------------------------------------------------------------

_ZH = ["我们决定用 multilingual 作为线上 embedder，因为中文区分度更好",
       "线上机每小时被 quant cron 的任务压垮，Engram 是受害者",
       "发布前必须过 LongMemEval A/B，不能只用中文语料验证",
       "读路径的两个同构缺陷：辅助信号驱逐主信号",
       "会话结论是记忆单元，逐轮抽取产出的是传记三元组",
       "算法改动必须更新架构优化地图",
       "工作台汇报只对实质性任务触发",
       "双 Agent 协作靠落盘交接，不共享会话上下文",
       "熔断线：两小时无交付即停下汇报",
       "验收要用证据，不用自评",
       "个人记忆路径和 benchmark 路径共用一套默认值是缝隙所在",
       "记忆自己长：watch 定时喂入"]
_EN = ["We decided to use the multilingual embedder for production because it ranks Chinese.",
       "The production box is crushed hourly by a quant cron job; Engram is the victim.",
       "Every algorithm change must pass a LongMemEval A/B before shipping.",
       "Two isomorphic read-path defects: auxiliary signals evicting the primary signal.",
       "Session conclusions are the memory unit; per-turn extraction yields biography triples.",
       "Architecture changes must update the optimization map.",
       "Report to the workbench only for substantial tasks.",
       "The two agents collaborate through files on disk, not shared context.",
       "Circuit breaker: two hours without a deliverable means stop and report.",
       "Accept on evidence, never on self-assessment.",
       "Personal and benchmark paths sharing one set of defaults is the seam.",
       "The memory feeds itself: a scheduled watch tick."]


def _seed(svc: MemoryService, user: str, texts: list[str]) -> None:
    sessions = [{"session_id": f"{user}-{i}", "messages": [{"role": "user", "content": t}]}
                for i, t in enumerate(texts)]
    svc.import_(user, sessions=sessions, consolidate=False, summarize=False)


def test_audit_embedder_blind_fires_on_hashing_non_ascii_store(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    _seed(svc, "zh", _ZH)
    audit = svc.audit("zh")
    rows = [f for f in audit["findings"] if f["kind"] == "embedder_blind"]
    assert len(rows) == 1 and audit["by_kind"]["embedder_blind"] == 1, audit
    row = rows[0]
    assert row["exact"] is True and row["embedder"] == "HashingEmbedder" and row["model"] is None
    # The rule counts letters, not punctuation: lines that lean on ASCII names ("multilingual",
    # "quant cron", "LongMemEval A/B") come out roughly half-and-half and are rightly NOT called blind.
    # Nine of twelve still are, which is the 20% share several times over.
    assert row["checked"] >= 10 and row["blind"] == row["episodes_blind"] >= 8
    assert row["ratio"] >= 0.2 and "fact_id" not in row
    assert row["text"] == f"HashingEmbedder cannot rank {round(row['ratio'] * 100)}% of {row['checked']} memories"
    assert row["migrate"] in row["action"] and "bge-small-zh" in row["action"]
    assert audit["total_findings"] >= 1
    # the agent sees the same verdict, with the command an operator can run
    actions = svc.agent_status("zh")["recommended_next_actions"]
    assert any("ENGRAM_EMBEDDER=multilingual ENGRAM_REEMBED_ON_MISMATCH=1" in a
               and "HashingEmbedder" in a for a in actions), actions


def test_audit_no_embedder_blind_on_english_store(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    _seed(svc, "en", _EN)
    assert "embedder_blind" not in svc.audit("en")["by_kind"]
    assert not any("cannot be ranked" in a for a in svc.agent_status("en")["recommended_next_actions"])
    # and below the floor of 10 memories nothing fires: a 3-line store is not evidence of anything
    _seed(svc, "tiny", _ZH[:3])
    assert "embedder_blind" not in svc.audit("tiny")["by_kind"]


def test_audit_no_embedder_blind_for_multilingual_embedder(tmp_path):
    """The rule keys on the tokenizer, not on the corpus alone: a multilingual model ranks CJK fine, an
    English-only sentence-transformer degrades (reported, but not as an exact verdict)."""
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    _seed(svc, "zh", _ZH)

    class SentenceTransformerEmbedder:  # duck-typed stand-in; the real one needs sentence-transformers
        def __init__(self, model_name: str):
            self.model_name = model_name

    svc.embedder = SentenceTransformerEmbedder("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    assert "embedder_blind" not in svc.audit("zh")["by_kind"]

    svc.embedder = SentenceTransformerEmbedder("BAAI/bge-small-en-v1.5")
    rows = [f for f in svc.audit("zh")["findings"] if f["kind"] == "embedder_blind"]
    assert len(rows) == 1 and rows[0]["exact"] is False
    assert rows[0]["model"] == "BAAI/bge-small-en-v1.5" and rows[0]["embedder"] == "SentenceTransformerEmbedder"

    class LiteLLMEmbedder:  # any other embedder: never fires (we have no evidence about it)
        model_name = "text-embedding-3-small"

    svc.embedder = LiteLLMEmbedder()
    assert "embedder_blind" not in svc.audit("zh")["by_kind"]


def test_audit_no_embedder_blind_on_code_heavy_ascii_store(tmp_path):
    """Punctuation is not a script. A store of JSON / SQL / shell facts is 100% [a-z0-9]-tokenizable —
    the first version counted braces and pipes against the text and told the owner a third of their
    memories were unrankable."""
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    code = ['{"a": 1, "b": [2, 3], "c": {"d": null}}', "if (!ok) { return -1; }",
            "SELECT * FROM t WHERE x = 1;", "ls -la | grep -v '^d' | wc -l",
            "export FOO=bar && ./run.sh --flag=1", "def f(x): return (x + 1) * 2",
            "curl -s http://127.0.0.1:8456/health | jq .", "git log --oneline -5 | head",
            "npm run build && npm test -- --ci", "docker ps --format '{{.Names}}'",
            "kubectl get pods -n default -o wide", "python3 -m pytest tests/ -q"]
    _seed(svc, "code", code)
    assert "embedder_blind" not in svc.audit("code")["by_kind"]


def test_audit_embedder_blind_fires_on_two_large_chinese_sessions(tmp_path):
    """The owner's real backfill: a couple of long transcripts, zero facts. Item count is 2, letter mass
    is thousands — the verdict must come from the mass."""
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    long_zh = ["，".join(_ZH) * 8, "。".join(reversed(_ZH)) * 8]
    _seed(svc, "big", long_zh)
    audit = svc.audit("big")
    rows = [f for f in audit["findings"] if f["kind"] == "embedder_blind"]
    assert len(rows) == 1, audit["by_kind"]
    assert rows[0]["checked"] == 2 and rows[0]["dark_chars"] > 2000
    assert rows[0]["ratio"] >= 0.9

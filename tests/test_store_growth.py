"""A desktop store must not grow with every save: a grown agent session replaces its episode, and a save
appends to the episodes log instead of copying and rewriting it. All offline."""
from __future__ import annotations

import json
import os

from engram.embed import HashingEmbedder
from engram.memory import Memory
from engram.store import persist


def _agent(sid: str, turns: list[str]) -> dict:
    return {"session_id": sid, "metadata": {"source": "agent_session"},
            "messages": [{"role": "user" if i % 2 == 0 else "assistant", "content": t}
                         for i, t in enumerate(turns)]}


def test_grown_agent_session_replaces_its_episode_in_place():
    m = Memory(embedder=HashingEmbedder(64))
    first = m.import_messages([_agent("rollout-1", ["定一下报价", "好的"])], user_id="u")
    (ep,) = m.episodes_doc.values()
    ep_id = ep.id
    m.summarize_episodes([ep])
    grown = m.import_messages([_agent("rollout-1", ["定一下报价", "好的", "报价按 12 万走"])], user_id="u")
    assert first["episodes"] == 1 and grown["episodes"] == 1 and grown["grown"] == 1
    (ep,) = m.episodes_doc.values()
    assert ep.id == ep_id and "12 万" in ep.content
    assert ep.summary and "12 万" in ep.summary  # re-summarized over the whole transcript
    # re-sending the same length is still a no-op
    assert m.import_messages([_agent("rollout-1", ["定一下报价", "好的", "报价按 12 万走"])],
                             user_id="u")["skipped"] == 1


def test_other_sources_still_append_a_new_episode():
    m = Memory(embedder=HashingEmbedder(64))
    m.import_messages([{"session_id": "s", "messages": [{"role": "user", "content": "a"}]}], user_id="u")
    m.import_messages([{"session_id": "s", "messages": [{"role": "user", "content": "a"},
                                                         {"role": "user", "content": "b"}]}], user_id="u")
    assert len(m.episodes_doc.values()) == 2


def _manifest(path: str) -> dict:
    with open(os.path.join(path, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_save_appends_episodes_without_backing_up_the_log(tmp_path, monkeypatch):
    path = str(tmp_path / "s")
    m = Memory(embedder=HashingEmbedder(64))
    m.add("first transcript", session_id="a")
    m.save(path)
    size1 = os.path.getsize(os.path.join(path, "episodes.jsonl"))
    assert _manifest(path)["sizes"]["episodes"] == size1

    copied: list[str] = []
    real_copy = persist._copy_synced
    monkeypatch.setattr(persist, "_copy_synced",
                        lambda src, dst: (copied.append(os.path.basename(src)), real_copy(src, dst)))
    m.add("second transcript", session_id="b")
    m.save(path)
    assert "episodes.jsonl" not in copied and "manifest.json" in copied
    size2 = os.path.getsize(os.path.join(path, "episodes.jsonl"))
    assert size2 > size1 and _manifest(path)["counts"]["episodes"] == 2

    copied.clear()
    m.save(path)  # nothing changed: the log is left alone
    assert os.path.getsize(os.path.join(path, "episodes.jsonl")) == size2 and "episodes.jsonl" not in copied

    reopened = Memory.open(path, embedder=HashingEmbedder(64))
    assert sorted(ep.content for ep in reopened.episodes_doc.values()) == ["first transcript",
                                                                           "second transcript"]


def test_changed_episode_appends_an_override_row_that_wins_on_load(tmp_path):
    path = str(tmp_path / "s")
    m = Memory(embedder=HashingEmbedder(64))
    ep = m.add("v1", session_id="a")
    m.save(path)
    ep.summary = "now summarized"
    m.save(path)
    assert _manifest(path)["counts"]["episodes"] == 2  # one live episode, one superseded row
    reopened = Memory.open(path, embedder=HashingEmbedder(64))
    (only,) = reopened.episodes_doc.values()
    assert only.summary == "now summarized"
    assert reopened._persist_episodes["dead"] == 1


def test_interrupted_append_is_invisible_and_cut_by_the_next_save(tmp_path, monkeypatch):
    path = str(tmp_path / "s")
    m = Memory(embedder=HashingEmbedder(64))
    m.add("committed", session_id="a")
    m.save(path)
    committed = os.path.getsize(os.path.join(path, "episodes.jsonl"))

    def crash(*_a, **_k):
        raise RuntimeError("power cut before the manifest")
    m.add("lost", session_id="b")
    monkeypatch.setattr(persist, "_write_jsonl_sized", crash)  # facts are written after the append
    try:
        m.save(path)
    except RuntimeError:
        pass
    monkeypatch.undo()
    assert os.path.getsize(os.path.join(path, "episodes.jsonl")) > committed  # the append hit the disk...
    reopened = Memory.open(path, embedder=HashingEmbedder(64))
    assert [ep.content for ep in reopened.episodes_doc.values()] == ["committed"]  # ...but never committed
    reopened.add("next", session_id="c")
    reopened.save(path)
    again = Memory.open(path, embedder=HashingEmbedder(64))
    assert sorted(ep.content for ep in again.episodes_doc.values()) == ["committed", "next"]
    assert _manifest(path)["counts"]["episodes"] == 2


def test_delete_rewrites_and_compacts(tmp_path):
    path = str(tmp_path / "s")
    m = Memory(embedder=HashingEmbedder(64))
    keep = m.add("keep", session_id="a")
    drop = m.add("drop", session_id="b")
    m.save(path)
    keep.summary = "s"
    m.save(path)
    assert _manifest(path)["counts"]["episodes"] == 3
    m.episodes_doc.delete(drop.id)
    m.episodes_vec.delete(drop.id)
    m.save(path)
    assert _manifest(path)["counts"]["episodes"] == 1
    assert [ep.content for ep in Memory.open(path, embedder=HashingEmbedder(64)).episodes_doc.values()] == ["keep"]


def test_agent_session_text_is_capped_to_a_head_and_tail_window():
    from engram.config import Config

    m = Memory(embedder=HashingEmbedder(64), config=Config(agent_session_max_chars=1000))
    turns = ["开场：先定范围"] + [f"中段讨论第{i}轮" for i in range(400)] + ["结论：报价按 12 万执行"]
    m.import_messages([_agent("rollout-9", turns)], user_id="u")
    (ep,) = m.episodes_doc.values()
    assert len(ep.content) <= 1000 + 40 and "开场" in ep.content and "12 万" in ep.content
    assert "中段省略" in ep.content
    # a re-send of the same full transcript is still recognised, though only its window was stored
    assert m.import_messages([_agent("rollout-9", turns)], user_id="u")["skipped"] == 1

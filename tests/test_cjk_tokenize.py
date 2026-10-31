"""CJK text is indexed as overlapping bigrams, and stores saved before that upgrade in place."""
import json
import os

import pytest

from engram import Memory
from engram.embed import HashingEmbedder
from engram.store.persist import EmbedderMismatchError
from engram.util import content_term, cosine, has_cjk, tokenize


def test_tokenize_splits_cjk_runs_into_bigrams_and_leaves_ascii_alone():
    assert tokenize("报价单") == ["报价", "价单"]
    assert tokenize("我") == ["我"]
    assert tokenize("Engram works, 2026!") == ["engram", "works", "2026"]
    assert tokenize("和张三签合同 v2") == ["和张", "张三", "三签", "签合", "合同", "v2"]


def test_content_term_keeps_chinese_bigrams_and_drops_short_ascii():
    assert content_term("合同") and content_term("engram")
    assert not content_term("我") and not content_term("of")
    assert has_cjk("abc 中") and not has_cjk("abc")


def test_hashing_ranks_a_two_character_chinese_query():
    emb = HashingEmbedder()
    q = emb.embed("报价")
    hit = emb.embed("报价单已经发给客户了")
    miss = emb.embed("下周三开项目复盘会")
    assert any(q) and cosine(q, hit) > cosine(q, miss)


def _legacy_store(path: str) -> None:
    mem = Memory(embedder=HashingEmbedder())
    mem.add("合同下周签，报价单已发", session_id="s1")
    mem.add("The quarterly review is on Friday", session_id="s2")
    mem.save(path)
    manifest_path = os.path.join(path, "manifest.json")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest["embedder_id"] = "HashingEmbedder"  # what every hashing store recorded before CJK bigrams
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    # ...and under the old tokenizer a Chinese text hashed to an all-zero vector
    episodes_path = os.path.join(path, "episodes.jsonl")
    with open(episodes_path, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    for row in rows:
        if has_cjk(row["content"]):
            row["embedding"] = [0.0] * len(row["embedding"])
    with open(episodes_path, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def test_legacy_hashing_store_opens_and_reembeds_only_cjk_items(tmp_path):
    path = str(tmp_path / "store")
    _legacy_store(path)
    mem = Memory.open(path, embedder=HashingEmbedder())
    eps = {ep.session_id: ep for ep in mem.episodes_doc.values()}
    assert any(eps["s1"].embedding)
    assert eps["s1"].embedding == HashingEmbedder().embed(eps["s1"].content)
    mem.save()
    with open(os.path.join(path, "manifest.json"), encoding="utf-8") as fh:
        assert json.load(fh)["embedder_id"] == "hashing-cjk2"


def test_other_embedder_mismatch_still_refuses(tmp_path):
    path = str(tmp_path / "store")
    _legacy_store(path)
    with open(os.path.join(path, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest["embedder_id"] = "some-other-model"
    with open(os.path.join(path, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    with pytest.raises(EmbedderMismatchError):
        Memory.open(path, embedder=HashingEmbedder())

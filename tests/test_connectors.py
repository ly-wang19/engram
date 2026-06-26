"""Connectors + batch import — pure offline (hashing embedder, rule extractor), zero setup."""
from __future__ import annotations

import json

from engram import Memory
from engram.connectors import parse, sniff
from engram.connectors.base import to_epoch

# A minimal but realistic ChatGPT export: a 3-node active thread under a root, plus a hidden system node.
CHATGPT = [{
    "title": "Trip planning",
    "create_time": 1700000000.0,
    "current_node": "n3",
    "mapping": {
        "root": {"id": "root", "message": None, "parent": None, "children": ["sys"]},
        "sys": {"id": "sys", "parent": "root", "children": ["n1"],
                "message": {"author": {"role": "system"}, "create_time": 1700000000.0,
                            "metadata": {"is_visually_hidden_from_conversation": True},
                            "content": {"content_type": "text", "parts": [""]}}},
        "n1": {"id": "n1", "parent": "sys", "children": ["n2"],
               "message": {"author": {"role": "user"}, "create_time": 1700000000.0,
                           "content": {"content_type": "text",
                                       "parts": ["I am planning a trip to Tokyo in March."]}}},
        "n2": {"id": "n2", "parent": "n1", "children": ["n3"],
               "message": {"author": {"role": "assistant"}, "create_time": 1700000100.0,
                           "content": {"content_type": "text", "parts": ["Tokyo is lovely in spring."]}}},
        "n3": {"id": "n3", "parent": "n2", "children": [],
               "message": {"author": {"role": "user"}, "create_time": 1700000200.0,
                           "content": {"content_type": "text", "parts": ["My budget is 3000 dollars."]}}},
    },
}]


def test_parse_chatgpt_reconstructs_active_thread():
    sessions = parse(CHATGPT, format="chatgpt")
    assert len(sessions) == 1
    s = sessions[0]
    assert s.title == "Trip planning"
    # system node hidden -> 3 visible turns, in chronological order
    assert [m.speaker for m in s.messages] == ["user", "assistant", "user"]
    assert "Tokyo" in s.messages[0].content
    assert s.messages[0].event_time == 1700000000.0
    assert s.start_time() == 1700000000.0


def test_parse_chatgpt_via_json_string_and_autosniff():
    assert sniff(json.dumps(CHATGPT)) == "chatgpt"
    sessions = parse(json.dumps(CHATGPT))  # auto
    assert len(sessions) == 1 and len(sessions[0].messages) == 3


def test_parse_openai_messages():
    arr = [{"role": "user", "content": "My name is Wei."},
           {"role": "assistant", "content": "Nice to meet you, Wei."},
           {"role": "user", "content": [{"type": "text", "text": "I live in Shenzhen."}]}]
    sessions = parse(arr, format="messages")
    assert len(sessions) == 1
    msgs = sessions[0].messages
    assert len(msgs) == 3
    assert msgs[2].content == "I live in Shenzhen."  # multimodal content-parts flattened
    assert sniff(arr) == "messages"


def test_parse_records_groups_by_session():
    recs = [
        {"content": "first in A", "session_id": "A", "timestamp": "2024-01-01"},
        {"content": "first in B", "session_id": "B", "timestamp": "2024-01-02"},
        {"content": "second in A", "session_id": "A", "timestamp": "2024-01-03"},
    ]
    sessions = parse(recs, format="records")
    by_id = {s.session_id: s for s in sessions}
    assert set(by_id) == {"A", "B"}
    assert [m.content for m in by_id["A"].messages] == ["first in A", "second in A"]
    assert by_id["A"].start_time() == to_epoch("2024-01-01")


def test_parse_jsonl():
    text = "\n".join(json.dumps(r) for r in [
        {"role": "user", "content": "hello", "session_id": "s1"},
        {"role": "assistant", "content": "hi", "session_id": "s1"},
    ])
    assert sniff(text) == "jsonl"
    sessions = parse(text, format="jsonl")
    assert len(sessions) == 1 and len(sessions[0].messages) == 2


def test_parse_transcript_speaker_lines_and_freeform():
    tagged = "Alice: Hey there\nBob: Hi Alice\nhow are you?"
    s = parse(tagged, format="transcript")[0]
    assert [m.speaker for m in s.messages] == ["Alice", "Bob"]
    assert s.messages[1].content == "Hi Alice\nhow are you?"  # continuation appended

    free = "Just some freeform notes.\nNo speakers here."
    assert sniff(free) == "transcript"
    s2 = parse(free)[0]
    assert len(s2.messages) == 1 and "freeform" in s2.messages[0].content


def test_to_epoch_forms():
    assert to_epoch(1700000000) == 1700000000.0
    assert to_epoch(1700000000000) == 1700000000.0  # ms rescaled
    assert to_epoch("2024-01-15") == to_epoch("2024-01-15T00:00:00Z")
    assert to_epoch(None) is None and to_epoch("") is None


def test_import_messages_end_to_end_offline():
    mem = Memory()
    sessions = parse(CHATGPT, format="chatgpt")
    stats = mem.import_messages(sessions, user_id="me")
    assert stats["sessions"] == 1 and stats["episodes"] == 1
    # the raw detail is retrievable through the lean read path (deterministic, no LLM needed)
    ctx = mem.lean_context("Where is the trip?", user_id="me")
    assert "Tokyo" in ctx


def test_timestamped_records_preserve_message_times_on_import():
    mem = Memory()
    records = [
        {"session_id": "career", "content": "Wei works at Tencent.", "event_time": 1_700_000_000.0},
        {"session_id": "career", "content": "Wei works at Moonshot AI.", "event_time": 1_700_086_400.0},
    ]
    stats = mem.import_data(records, format="records", user_id="u", summarize=False)

    assert stats["sessions"] == 1 and stats["episodes"] == 2
    chain = mem.history("u", "works_at", user_id="u")
    assert sorted(f.valid_at for f in chain) == [1_700_000_000.0, 1_700_086_400.0]
    assert "Tencent" in mem.as_of("Where does Wei work?", 1_700_000_001.0, user_id="u").answer()
    assert "Moonshot AI" in mem.search("Where does Wei work?", user_id="u").answer()


def test_import_messages_dict_sessions_coerce_iso_timestamps():
    mem = Memory()
    stats = mem.import_messages([{
        "session_id": "career",
        "messages": [{
            "role": "user",
            "content": "Wei works at Tencent.",
            "event_time": "2023-11-14T00:00:00Z",
        }],
    }], user_id="u", summarize=False)

    assert stats["episodes"] == 1 and stats["facts_added"] == 1
    fact = next(iter(mem.fact_store.values()))
    assert isinstance(fact.valid_at, float)
    assert fact.valid_at == to_epoch("2023-11-14T00:00:00Z")
    assert "Tencent" in mem.search("Where does Wei work?", user_id="u").answer()


def test_import_messages_dict_sessions_preserve_message_times():
    mem = Memory()
    stats = mem.import_messages([{
        "session_id": "career",
        "messages": [
            {"role": "user", "content": "Wei works at Tencent.", "event_time": 1_700_000_000.0},
            {"role": "user", "content": "Wei works at Moonshot AI.", "event_time": 1_702_592_000.0},
        ],
    }], user_id="u", summarize=False)

    assert stats["sessions"] == 1 and stats["episodes"] == 2
    chain = mem.history("u", "works_at", user_id="u")
    assert sorted(f.valid_at for f in chain) == [1_700_000_000.0, 1_702_592_000.0]
    assert "Tencent" in mem.as_of("Where does Wei work?", 1_700_864_000.0, user_id="u").answer()
    assert "Moonshot AI" in mem.search("Where does Wei work?", user_id="u").answer()


def test_import_data_autosniff_and_synthetic_clock():
    mem = Memory()
    arr = [{"role": "user", "content": "I work at Tencent."}]
    stats = mem.import_data(arr, user_id="u2", base_time=1_700_000_000.0)
    assert stats["episodes"] == 1
    ep = list(mem.episodes_doc.values())[0]
    assert ep.metadata.get("date")  # synthetic date stamped even without source timestamps

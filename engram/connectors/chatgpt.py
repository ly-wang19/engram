"""ChatGPT data-export connector.

ChatGPT's `conversations.json` is a list of conversations, each a *tree* of message nodes in a
`mapping` dict (so it can represent edited/branched turns). We reconstruct the **active linear thread**
by walking `parent` pointers up from `current_node`, which is what the user actually sees — then fall
back to a time-sorted sweep of all nodes if the tree is malformed.
"""
from __future__ import annotations

from typing import Any

from .base import ImportMessage, ImportSession, extract_text, to_epoch


def _node_text(message: dict) -> str:
    """Pull display text out of a ChatGPT message node, across content types (text / multimodal / code)."""
    content = message.get("content")
    if isinstance(content, dict) and content.get("content_type") == "code":
        return extract_text(content.get("text", ""))
    return extract_text(content)


def _is_hidden(message: dict) -> bool:
    meta = message.get("metadata") or {}
    return bool(meta.get("is_visually_hidden_from_conversation"))


def _linear_thread(mapping: dict, current_node: Any) -> list[dict]:
    """Active path: follow parent links from current_node to the root, then reverse to chronological."""
    path: list[dict] = []
    seen: set[str] = set()
    node_id = current_node
    while node_id and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        node = mapping[node_id]
        msg = node.get("message")
        if msg:
            path.append(msg)
        node_id = node.get("parent")
    path.reverse()
    return path


def _all_messages_sorted(mapping: dict) -> list[dict]:
    """Fallback when there's no usable current_node: every message node, ordered by create_time."""
    msgs = [n["message"] for n in mapping.values() if isinstance(n, dict) and n.get("message")]
    return sorted(msgs, key=lambda m: m.get("create_time") or 0.0)


def parse_conversation(conv: dict, index: int = 0) -> ImportSession:
    """One ChatGPT conversation -> one ImportSession."""
    mapping = conv.get("mapping") or {}
    nodes = _linear_thread(mapping, conv.get("current_node")) or _all_messages_sorted(mapping)

    messages: list[ImportMessage] = []
    for msg in nodes:
        if not isinstance(msg, dict) or _is_hidden(msg):
            continue
        role = ((msg.get("author") or {}).get("role")) or "user"
        if role == "system":
            continue  # hidden system primer — not user memory
        text = _node_text(msg).strip()
        if not text:
            continue
        messages.append(ImportMessage(
            content=text, speaker=role, event_time=to_epoch(msg.get("create_time"))
        ))

    title = conv.get("title") or f"conversation {index + 1}"
    sid = conv.get("conversation_id") or conv.get("id") or f"chatgpt_{index}"
    start = to_epoch(conv.get("create_time"))
    return ImportSession(session_id=str(sid), messages=messages, event_time=start, title=str(title),
                         metadata={"source": "chatgpt"})


def parse_chatgpt(obj: Any) -> list[ImportSession]:
    """Parse a ChatGPT export. Accepts the top-level list, a `{conversations: [...]}` wrapper, or a
    single conversation object. Empty conversations (no surviving turns) are dropped."""
    if isinstance(obj, dict):
        if "conversations" in obj:
            obj = obj["conversations"]
        elif "mapping" in obj:
            obj = [obj]
    if not isinstance(obj, list):
        raise ValueError("ChatGPT export must be a list of conversations (or a {conversations:[...]}).")
    sessions = [parse_conversation(c, i) for i, c in enumerate(obj) if isinstance(c, dict)]
    return [s for s in sessions if s.messages]

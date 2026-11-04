"""Local history parsing (D9: no export caps, only a single message is limited) and graph-backed,
explicitly reviewed onboarding.

This module never calls a model, connector, or network. Imported text is historical
reported material, including claims made by assistants. It cannot mint permission.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from urllib.parse import urlparse

from skuld.kernel.schemas import AuthorityClass, ModalState, canonical_hash

MAX_TEXT = 100000  # D9: the only limit; a longer message is counted and skipped, never truncated (CHANGE_NOTE_8)
QUESTIONS = (
    {"id": "direction", "kind": "Goal", "label": "你想走向哪里", "question": "最近最想推进的一件事是什么？", "hint": "一个具体目标就好。", "placeholder": "例如：把产品原型做成可以真正使用的版本"},
    {"id": "open_loop", "kind": "Task", "label": "还悬着的事", "question": "有什么还没完成，或有人正在等你的事？", "hint": "可以写人、时间或你希望达到的结果。", "placeholder": "这周还需要完成……"},
    {"id": "people", "kind": "Relationship", "label": "重要的人", "question": "现在有哪些对你重要的人或合作关系？", "hint": "只留下你愿意告诉我的部分。", "placeholder": "谁与你一起做什么，最近怎样……"},
    {"id": "constraints", "kind": "Fact", "label": "现实的边界", "question": "近期有哪些时间、精力或资源限制？", "hint": "不知道或不想回答，都可以跳过。", "placeholder": "这段时间可以投入……，但……"},
    {"id": "context", "kind": "Fact", "label": "你所在的世界", "question": "你的工作或生活处境，最该让我知道什么？", "hint": "一句话也能成为好的开始。", "placeholder": "最近我在……"},
    {"id": "signals", "kind": "Preference", "label": "值得留意的变化", "question": "哪些变化会影响你的计划？", "hint": "这会帮助我判断什么值得进一步了解，不会自动连接来源。", "placeholder": "如果……变化，我就需要重新考虑……"},
    {"id": "style", "kind": "Preference", "label": "我们怎样相处", "question": "你希望我怎样交流、陪伴或提供帮助？", "hint": "直接一点、一起推敲、留些空间，按你的方式。", "placeholder": "我希望你……"},
    {"id": "boundaries", "kind": "Boundary", "label": "你的界限", "question": "有哪些事要先问你，或暂时别碰？", "hint": "这里记录边界；不会授予发消息、付款或操作账户的权限。", "placeholder": "涉及……之前，先问我"},
)
QUESTION_BY_ID = {q["id"]: q for q in QUESTIONS}


def _time(value):
    if value is None or value == "": return None
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        if isinstance(value, str):
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if date.tzinfo is None: return None
            return date.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError): pass
    return None


def _text(parts, warnings, stats):
    kept = []
    for part in parts:
        if isinstance(part, str): kept.append(part)
        elif isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str): kept.append(part["text"])
        else:
            warnings.add("附件、图片、工具数据与非文本内容没有导入。"); stats["non_text_blocks"] += 1
    return "\n".join(kept).strip()


def _fits(value, warnings, stats):
    if len(value) <= MAX_TEXT: return True
    warnings.add("超过 100,000 字符的单条消息没有导入，已计数。"); stats["oversized"] += 1
    return False


def _message(message_id, role, text, timestamp):
    return {"id": str(message_id), "role": role, "text": text, "effective_at": _time(timestamp),
            "historical": True, "provider_eligible": False, "permission_grant": False}


def _chatgpt_messages(conversation, warnings, stats=None):
    stats = stats if stats is not None else {"oversized": 0, "non_text_blocks": 0}
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict) or not mapping: raise ValueError("ChatGPT 对话缺少有效 mapping。")
    current = conversation.get("current_node")
    if current is None:
        parents = {n.get("parent") for n in mapping.values() if isinstance(n, dict) and n.get("parent")}
        leaves = [k for k in mapping if k not in parents]
        if len(leaves) != 1: raise ValueError("包含分支的 ChatGPT 对话需要 current_node，无法替你选择历史分支。")
        current = leaves[0]
    path, seen = [], set()
    while current is not None:
        if current in seen or current not in mapping: raise ValueError("ChatGPT 对话的历史分支损坏或存在循环。")
        seen.add(current); node = mapping[current]
        if not isinstance(node, dict): raise ValueError("ChatGPT mapping 结点格式不支持。")
        path.append((current, node.get("message"))); current = node.get("parent")
    if len(seen) < len(mapping): warnings.add("只导入 ChatGPT 当前选中分支；其他回复分支未导入。")
    messages = []
    for key, message in reversed(path):
        if message is None: continue
        if not isinstance(message, dict): raise ValueError("ChatGPT message 格式不支持。")
        author = message.get("author") or {}
        if not isinstance(author, dict): raise ValueError("ChatGPT author 格式不支持。")
        role = author.get("role")
        if role not in ("user", "assistant"):
            warnings.add("系统消息与工具消息没有导入，也不构成权限。"); continue
        content = message.get("content") or {}
        if not isinstance(content, dict): raise ValueError("ChatGPT content 格式不支持。")
        parts = content.get("parts")
        if not isinstance(parts, list):
            warnings.add("无法识别的非文本消息没有导入。"); continue
        value = _text(parts, warnings, stats)
        if value and _fits(value, warnings, stats): messages.append(_message(message.get("id") or key, role, value, message.get("create_time")))
    return messages


def _claude_messages(conversation, warnings, stats=None):
    stats = stats if stats is not None else {"oversized": 0, "non_text_blocks": 0}
    raw = conversation.get("chat_messages")
    if not isinstance(raw, list): raise ValueError("Claude 对话缺少有效 chat_messages。")
    messages = []
    for index, message in enumerate(raw):
        if not isinstance(message, dict): raise ValueError("Claude chat_messages 格式不支持。")
        role = {"human": "user", "assistant": "assistant"}.get(message.get("sender"))
        if role is None:
            warnings.add("系统消息与工具消息没有导入，也不构成权限。"); continue
        blocks = message.get("content") if isinstance(message.get("content"), list) else []
        if isinstance(message.get("text"), str) and message["text"].strip(): value = _text([message["text"]], warnings, stats)
        else: value = _text([b for b in blocks if isinstance(b, dict) and b.get("type") == "text"], warnings, stats)
        extra = sum(1 for b in blocks if not isinstance(b, dict) or b.get("type") != "text") + len(message.get("attachments") or []) + len(message.get("files") or [])
        if extra:
            warnings.add("附件、图片、工具数据与非文本内容没有导入。"); stats["non_text_blocks"] += extra
        if value and _fits(value, warnings, stats): messages.append(_message(message.get("uuid") or str(index), role, value, message.get("created_at")))
    return messages


def parse_export(payload):
    """Parse documented JSON shapes held in memory (the UI preview path; files stream through the
    runtime's preview_import). No persistence and no model processing."""
    try: json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError) as exc: raise ValueError("请选择有效的 JSON 导出文件。") from exc
    rows = payload.get("conversations") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise ValueError("支持 ChatGPT mapping 或 Claude chat_messages 的对话数组；不是任意聊天 JSON。")
    kinds = {"chatgpt_mapping" if isinstance(c, dict) and "mapping" in c else "claude_chat_messages" if isinstance(c, dict) and "chat_messages" in c else "unsupported" for c in rows}
    if len(kinds) != 1 or "unsupported" in kinds: raise ValueError("文件格式不支持或混合了不同格式；请使用单一 ChatGPT/Claude 导出 JSON。")
    format_name = next(iter(kinds)); warnings, conversations, seen_ids = set(), [], set(); stats = {"oversized": 0, "non_text_blocks": 0}
    counts = {"conversations": 0, "messages": 0, "user": 0, "assistant": 0}
    for index, conversation in enumerate(rows):
        cid = str(conversation.get("id") or conversation.get("uuid") or conversation.get("conversation_id") or ("conversation-" + str(index)))
        if cid in seen_ids: raise ValueError("导出文件包含重复对话 ID，无法安全合并。")
        seen_ids.add(cid)
        messages = (_chatgpt_messages if format_name == "chatgpt_mapping" else _claude_messages)(conversation, warnings, stats)
        ids = [m["id"] for m in messages]
        if len(set(ids)) != len(ids): raise ValueError("对话中存在重复消息 ID，无法安全导入。")
        if not messages: continue
        title = conversation.get("title") or conversation.get("name") or "未命名对话"
        if not isinstance(title, str): title = "未命名对话"
        conversations.append({"id": cid, "title": title[:300], "messages": messages})
        counts["conversations"] += 1; counts["messages"] += len(messages)
        for message in messages:
            counts[message["role"]] += 1
            if message["effective_at"] is None: warnings.add("部分消息没有可确认的时间；将保留为历史时间未知，不会当作今天的事实。")
    if not conversations: raise ValueError("没有找到可导入的用户或助手文本消息。")
    result = {"format": format_name, "conversations": conversations, "counts": counts, "warnings": sorted(warnings), "oversized": stats["oversized"],
              "processing": "LOCAL_PARSE_ONLY", "external_provider_processing": False}
    result["digest"] = canonical_hash(result)
    return result


def _source_id(user, kind, ref): return "source_" + canonical_hash((user, kind, ref))[:40]


class OnboardingService:
    def __init__(self, runtime): self.j = runtime

    def processing_options(self, user):
        route = self.j.meta(user)["route"]; cfg = self.j.config.get("routes", {}).get(route, {})
        endpoint = cfg.get("base_url") or cfg.get("endpoint") or cfg.get("url") or ""
        host = urlparse(endpoint).hostname
        return {"route": route, "provider_host": host, "model": cfg.get("model"), "route_identity": self.j._route_identity(user) if cfg else None,
                "available": bool(host and cfg.get("key") and cfg.get("model") and self.j.config.get("product_provider_enabled", False)),
                "scope": "Only the exact current answers reviewed here; imported history remains local-only."}

    def status(self, user):
        self.j.meta(user)
        with self.j.lock:
            profiles = [n for n in self.j.nodes(user, "OnboardingProfile") if self.j.active(user, n)]
            profile = max(profiles, key=lambda n: (n.created_at, n.node_id)) if profiles else None
            episodes = self.j.nodes(user, "ConversationEpisode")
            return {"completed": bool(profile), "answers": dict(profile.content["answers"]) if profile else {},
                    "questions": list(QUESTIONS), "profile_id": profile.node_id if profile else None,
                    "provisional": True, "imported_messages": sum(1 for n in episodes if n.content.get("historical")),
                    "processing": self.processing_options(user), "provider_eligible": bool(profile and self.j.provider_eligible(user, profile))}

    def commit(self, user, answers, idempotency_key, processing_consent=None):
        if not isinstance(answers, dict) or any(k not in QUESTION_BY_ID for k in answers): raise ValueError("存在不支持的问题，未保存。")
        if any(not isinstance(v, str) or len(v) > 4000 for v in answers.values()): raise ValueError("每个回答须为不超过 4,000 字符的文本。")
        answers = {k: v.strip() for k, v in answers.items() if v.strip()}
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200: raise ValueError("需要有效的保存请求标识。")
        request_hash = canonical_hash({"answers": answers, "processing_consent": processing_consent})
        with self.j.lock, self.j.store.transaction():
            prior = self.j.graph.get_source_event(user, _source_id(user, "OnboardingReceipt", idempotency_key))
            if prior:
                if prior.content["input_hash"] != request_hash: raise ValueError("相同保存请求的内容发生变化，请使用新的请求。")
                return {**prior.content["result"], "replayed": True}
            options = self.processing_options(user)
            eligible = False
            if processing_consent:
                if processing_consent != {"route": options["route"], "route_identity": options["route_identity"], "allow": True} or not options["available"]:
                    raise ValueError("模型处理设置已经改变或尚未配置，请重新确认；本次未保存。")
                eligible = True
            source = self.j.source(user, "UserOnboarding", idempotency_key,
                                   {"answers": answers, "input_hash": request_hash, "speaker": "USER_INPUT", "reviewed_current_declarations": True,
                                    "provider_eligible": eligible, "processing_route": options["route"] if eligible else None, "processing_route_identity": options["route_identity"] if eligible else None,
                                    "permission_grant": False, "provisional": True})
            old_profiles = [n for n in self.j.nodes(user, "OnboardingProfile") if self.j.active(user, n)]
            previous = [n for n in self.j.nodes(user) if n.content.get("onboarding_dimension") and self.j.active(user, n)]
            goal_ids = []; nodes = {}
            for dimension, value in answers.items():
                question = QUESTION_BY_ID[dimension]
                content = {"text": value, "actor": "USER", "status": "active", "onboarding_dimension": dimension,
                           "provider_eligible": eligible, "processing_route": options["route"] if eligible else None, "processing_route_identity": options["route_identity"] if eligible else None,
                           "permission_grant": False, "provisional": True, "explicitly_reviewed": True}
                if question["kind"] in ("Goal", "Task"): content["goal"] = value
                node = self.j.node(user, question["kind"], "onboarding:" + dimension, content, source, authority=AuthorityClass.USER_FIRST_PERSON)
                nodes[dimension] = node
                if question["kind"] in ("Goal", "Task"): goal_ids.append(node.node_id)
            profile = self.j.node(user, "OnboardingProfile", "onboarding:profile", {"answers": answers, "provider_eligible": eligible,
                                  "provisional": True, "permission_grant": False}, source, authority=AuthorityClass.USER_FIRST_PERSON)
            revision = self.j.node(user, "Revision", "onboarding:revision:" + idempotency_key,
                                   {"text": "用户审阅后的起点更新", "predecessor_node_ids": [n.node_id for n in previous],
                                    "successor_node_ids": [n.node_id for n in nodes.values()], "provider_eligible": False}, source)
            for node in previous:
                self.j.edge(user, "SUPERSEDES" if node.content["onboarding_dimension"] in nodes else "WITHDRAWS", revision, node, source)
            for node in nodes.values(): self.j.edge(user, "CREATES", revision, node, source)
            for old in old_profiles: self.j.edge(user, "SUPERSEDES", profile, old, source)
            self.j.product.queue(user, source, "ONBOARDING", goal_ids=goal_ids)
            result = {"completed": True, "answers": answers, "profile_id": profile.node_id, "provisional": True,
                      "provider_eligible": eligible, "queued": True, "replayed": False}
            self.j.source(user, "OnboardingReceipt", idempotency_key, {"input_hash": request_hash, "result": result})
            return result

    def import_history(self, user, payload, preview_digest, conversation_ids, local_storage_consent):
        self.j.meta(user)
        if local_storage_consent is not True: raise ValueError("请先确认把所选历史保存在当前 Skuld 工作区。")
        preview = parse_export(payload)
        if preview["digest"] != preview_digest: raise ValueError("文件与已审阅的预览不一致，请重新预览。")
        if not isinstance(conversation_ids, list) or not conversation_ids or any(not isinstance(i, str) for i in conversation_ids):
            raise ValueError("请选择至少一段要保留的对话。")
        selected = set(conversation_ids)
        if not selected <= {c["id"] for c in preview["conversations"]}: raise ValueError("所选对话不在已审阅的导出文件中。")
        ref = canonical_hash({"digest": preview_digest, "conversations": sorted(selected)})
        with self.j.lock, self.j.store.transaction():
            prior = self.j.graph.get_source_event(user, _source_id(user, "HistoryImport", ref))
            if prior: return {**prior.content["result"], "replayed": True}
            imported = 0
            for conversation in preview["conversations"]:
                if conversation["id"] not in selected: continue
                source = self.j.source(user, "ImportedConversation", canonical_hash([preview["format"], conversation]),
                                       {"format": preview["format"], "conversation_id": conversation["id"], "title": conversation["title"],
                                        "historical": True, "provider_eligible": False, "permission_grant": False})
                for message in conversation["messages"]:
                    self.j.node(user, "ConversationEpisode", "import:" + canonical_hash([preview["format"], conversation["id"], message["id"]]),
                                {**message, "speaker": message["role"].upper(), "origin": preview["format"], "conversation_title": conversation["title"],
                                 "historical_time_unknown": message["effective_at"] is None, "not_current_fact": True, "not_action_approval": True},
                                source, valid=message["effective_at"], modal=ModalState.REPORTED, authority=AuthorityClass.HISTORICAL_REPORTED)
                    imported += 1
            result = {"imported_messages": imported, "imported_conversations": len(selected), "external_provider_processing": False, "replayed": False}
            self.j.source(user, "HistoryImport", ref, {"preview_digest": preview_digest, "result": result, "local_storage_consent": True, "provider_eligible": False})
            return result

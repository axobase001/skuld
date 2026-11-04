"""conversation channel: the user's finished conversation turns as observations, each once. The
turn itself is already in the graph; the channel lets the loop see that the user spoke."""
from __future__ import annotations

from skuld.channels.base import Channel
from skuld.kernel.primitives import nid


class ConversationChannel(Channel):
    kind = "conversation"

    @classmethod
    def check_config(cls, runtime, config):
        return {}

    def observe(self, cursor):
        rt, u = self.runtime, self.user_id
        cursor = cursor or {"after": None, "refs": []}
        done = [e for e in rt.graph.list_source_events(u, source_type="ConversationComplete")
                if cursor["after"] is None or e.received_at > cursor["after"]
                or (e.received_at == cursor["after"] and e.source_ref not in cursor["refs"])]
        turns = rt.graph.source_events_by_ids(u, [nid("source", u, "UserConversation", e.source_ref) for e in done])
        out = []
        for e in sorted(done, key=lambda e: (e.received_at, e.source_ref)):
            turn = turns.get(nid("source", u, "UserConversation", e.source_ref))
            out.append(self.observation("utterance:" + e.source_ref, turn.content["text"] if turn else None, e.received_at,
                                        {"turn_source_id": turn.source_event_id if turn else None}))
        if done:
            last = max(e.received_at for e in done)
            refs = [e.source_ref for e in done if e.received_at == last]
            cursor = {"after": last, "refs": refs + (cursor["refs"] if last == cursor["after"] else [])}
        return out, cursor


__all__ = ["ConversationChannel"]

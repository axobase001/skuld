"""clock channel: time as an explicit channel (D2). It reports a responsibility whose due_at has
passed without completion, and a source key not sampled for longer than stale_after_seconds,
each once. CHANGE_NOTE_13 (2): it also reports due_soon:<responsibility id> once per mark at due - 24 h and due - 2 h,
for a mark crossed after the responsibility was created and before it is due (one observation per responsibility per
observe: the nearest mark). It reads the graph; it writes nothing and calls no model."""
from __future__ import annotations

from skuld.channels.base import Channel
from skuld.kernel.primitives import instant

DUE_SOON_HOURS = (24, 2)   # CHANGE_NOTE_13 (2): the same hours as arm B's reminders


class ClockChannel(Channel):
    kind = "clock"

    @classmethod
    def check_config(cls, runtime, config):
        stale = config.get("stale_after_seconds", 86400)
        if type(stale) is not int or stale < 60:
            raise ValueError("stale_after_seconds must be an integer >= 60")
        return {"stale_after_seconds": stale}

    def observe(self, cursor):
        rt, u = self.runtime, self.user_id
        now = instant(rt.clock())
        cursor = cursor or {"deadlines": [], "stale": {}}
        cursor.setdefault("due_soon", [])
        out = []
        for r in rt._open_deadlines(u, now):
            if r.node_id not in cursor["deadlines"]:
                out.append(self.observation("deadline:" + r.node_id, "passed", r.content["due_at"], {"responsibility_id": r.node_id}))
                cursor["deadlines"].append(r.node_id)
        for r, h, mark in rt._due_soon(u, now, DUE_SOON_HOURS):
            if f"{r.node_id}:{h}" not in cursor["due_soon"]:
                out.append(self.observation("due_soon:" + r.node_id, h, mark, {"responsibility_id": r.node_id, "hours_before_due": h}))
            cursor["due_soon"] += [f"{r.node_id}:{x}" for x in DUE_SOON_HOURS if x >= h and f"{r.node_id}:{x}" not in cursor["due_soon"]]
        for channel_id, key, last in rt._last_samples(u):
            mark = f"{channel_id}:{key}"
            age = (now - instant(last)).total_seconds()
            if age > self.config["stale_after_seconds"] and cursor["stale"].get(mark) != last:
                out.append(self.observation("stale:" + mark, int(age // 3600), last, {"last_sample_at": last}))
                cursor["stale"][mark] = last
        return out, cursor


__all__ = ["ClockChannel"]

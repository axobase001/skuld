"""S5 world environment: the objects as real channels the runtime polls. No HTTP: the product has exactly
one HTTP server (S0), so the world publishes through files only.
F1 quotes and F2 meetings: one calendar file per object, its SUMMARY carrying the current value.
F3 designs: one directory per object holding its design file; mtime = the change time. The directory channel reports the
scalar keys too (CHANGE_NOTE_13 (3))."""
import os
from datetime import timedelta
from pathlib import Path

ICS = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//skuld-sim//EN\r\nBEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{summary}\r\n"
       "DTSTART:20261110T090000Z\r\nDTEND:20261110T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


class Environment:
    def __init__(self, world, root, epoch):
        self.world, self.root, self.epoch = world, Path(root), epoch
        self.root.mkdir(parents=True, exist_ok=True)
        self.kind = world.objects[0]["channel_kind"]
        self.values = {oid: s["initial"] for oid, s in world.setup.items()}
        self.changed = {oid: 0.0 for oid in world.setup}
        self.versions = {oid: 1 for oid in world.setup}
        self.applied = 0

    def at(self, h):
        return self.epoch + timedelta(hours=h)

    def start(self):
        for oid in self.values:
            self._write(oid)

    def close(self):
        pass

    def _write(self, oid):
        s = self.world.setup[oid]
        if self.kind == "ics_calendar":
            (self.root / f"{oid}.ics").write_text(ICS.format(uid=oid, summary=self.values[oid]), encoding="utf-8", newline="")
        else:
            d = self.root / oid
            d.mkdir(exist_ok=True)
            p = d / s["key"]
            p.write_text(f"{self.values[oid]}\n（第 {self.versions[oid]} 版）\n", encoding="utf-8")
            stamp = self.at(self.changed[oid]).timestamp()
            os.utime(p, (stamp, stamp))

    def channel_config(self, oid):
        name = self.world.setup[oid]["name"]
        if self.kind == "ics_calendar":
            return {"path": str(self.root / f"{oid}.ics"), "name": name}
        return {"path": str(self.root / oid), "name": name, "scalar_keys": True}

    def add_channels(self, rt, u):
        return {oid: rt.add_channel(u, self.kind, self.channel_config(oid)) for oid in sorted(self.values)}

    def apply(self, h):
        """Apply every world event at or before hour h."""
        events = self.world.events
        while self.applied < len(events) and events[self.applied]["at_h"] <= h:
            e = events[self.applied]
            self.values[e["object_id"]], self.changed[e["object_id"]] = e["payload"]["value"], e["at_h"]
            self.versions[e["object_id"]] += 1
            self._write(e["object_id"])
            self.applied += 1


__all__ = ["Environment"]

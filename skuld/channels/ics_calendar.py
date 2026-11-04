"""ics_calendar channel: an authorized iCalendar file. Each VEVENT is reported as its summary, start
and end under event:<UID>:<field>. Parsing follows the April-version skills/calendar_ical.py
(archived), plus RFC 5545 line unfolding. Write: ADD_EVENT (risk 2) appends one VEVENT with a new
UID."""
from __future__ import annotations

import os
import re
from pathlib import Path

from skuld.channels.base import PolledChannel

FIELDS = (("summary", "SUMMARY"), ("start", "DTSTART"), ("end", "DTEND"))
UTC_BASIC = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
CRLF = chr(13) + chr(10)


def events(text):
    lines = []
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    out, current = [], None
    for line in lines:
        bare = line.strip()
        if bare == "BEGIN:VEVENT":
            current = {}
        elif bare == "END:VEVENT" and current is not None:
            out.append(current)
            current = None
        elif current is not None and ":" in line:
            name, _, value = line.partition(":")
            current[name.split(";")[0].strip().upper()] = value.strip()
    return out


class IcsCalendarChannel(PolledChannel):
    kind = "ics_calendar"
    OPERATIONS = {"READ": 0, "ADD_EVENT": 2}

    @classmethod
    def check_config(cls, runtime, config):
        path = Path(config["path"]).resolve()
        if not path.is_file():
            raise ValueError("calendar file not found")
        return {"path": str(path), "name": str(config.get("name") or path.name)}

    def sample(self):
        now, out = self.runtime.clock(), {}
        for event in events(Path(self.config["path"]).read_text(encoding="utf-8")):
            uid = event.get("UID")
            if not uid:
                continue
            for field, prop in FIELDS:
                if prop in event:
                    out[f"event:{uid}:{field}"] = (event[prop], now, {"property": prop})
        return out

    def check_operation(self, operation, parameters):
        if operation != "ADD_EVENT":
            raise ValueError(f"ics_calendar declares no write operation {operation}")
        if not isinstance(parameters, dict) or set(parameters) != {"uid", "summary", "start", "end"}:
            raise ValueError("ADD_EVENT takes exactly {uid, summary, start, end}")
        for k, v in parameters.items():
            if not isinstance(v, str) or not v.strip() or chr(13) in v or chr(10) in v:
                raise ValueError(f"ADD_EVENT {k} must be a one-line string")
        if not (UTC_BASIC.match(parameters["start"]) and UTC_BASIC.match(parameters["end"]) and parameters["start"] < parameters["end"]):
            raise ValueError("ADD_EVENT start and end are UTC basic times with start < end")

    def perform(self, operation, parameters, content):
        self.check_operation(operation, parameters)
        path = Path(self.config["path"])
        text = path.read_text(encoding="utf-8")
        if any(e.get("UID") == parameters["uid"] for e in events(text)):
            return {"ok": False, "effect": {"uid": parameters["uid"], "error": "event already exists"}}
        head, sep, tail = text.rpartition("END:VCALENDAR")
        if not sep:
            return {"ok": False, "effect": {"uid": parameters["uid"], "error": "not an iCalendar file"}}
        block = ["BEGIN:VEVENT", "UID:" + parameters["uid"], "SUMMARY:" + parameters["summary"],
                 "DTSTART:" + parameters["start"], "DTEND:" + parameters["end"], "END:VEVENT"]
        partial = path.with_name(path.name + ".skuld-partial")
        partial.write_text(head + CRLF.join(block) + CRLF + sep + tail, encoding="utf-8", newline="")
        os.replace(partial, path)
        return {"ok": True, "effect": {"uid": parameters["uid"], "added": True}}


__all__ = ["IcsCalendarChannel", "events"]

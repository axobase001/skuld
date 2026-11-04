"""Qt presents only persisted V4 state and actual Pi execution receipts."""

from __future__ import annotations

import json
import threading

from PySide6.QtCore import QObject, Property, Signal, Slot

from .v4_wiring import V4Terminal


class TerminalBridge(QObject):
    changed = Signal()
    completed = Signal(str, str)

    def __init__(self, terminal: V4Terminal):
        super().__init__()
        self.terminal = terminal
        self._dialogue: list[dict] = []
        self._trace = ""
        self._busy = False
        self.completed.connect(self._finish)
        self._refresh()

    def _refresh(self):
        state = self.terminal.state()
        actual = state["skuld"]
        self._state_text = json.dumps({
            "subject_id": actual["subject_id"], "mode": state["mode"],
            "local_profile": state["profile_id"],
            "route": actual["route"], "responsibilities": actual["responsibilities"],
            "product_state": actual["product_state"],
            "historical_world_evidence": state["historical_world_evidence"],
            "recent_outcomes": state["recent_outcomes"][-5:],
        }, ensure_ascii=False, indent=2, default=str)
        self._trace = json.dumps(self.terminal.trace[-10:] or state["recent_outcomes"][-10:],
            ensure_ascii=False, indent=2, default=str)
        self.changed.emit()

    @Property(str, notify=changed)
    def mode(self):
        return self.terminal.mode

    @Property("QVariantList", notify=changed)
    def dialogue(self):
        return self._dialogue

    @Property(str, notify=changed)
    def stateText(self):
        return self._state_text

    @Property(str, notify=changed)
    def traceText(self):
        return self._trace

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Slot(str)
    def requestMode(self, target):
        try:
            self.terminal.change_mode(target)
            self._refresh()
        except Exception as exc:
            self._dialogue.append({"speaker": "SYSTEM", "text": str(exc)})
            self.changed.emit()

    @Slot(str)
    def sendMessage(self, text):
        text = text.strip()
        if not text or self._busy:
            return
        self._dialogue.append({"speaker": "USER", "text": text})
        self._busy = True
        self.changed.emit()

        def run():
            try:
                if text.startswith("/read "):
                    reply = self.terminal.read(text[6:].strip())
                    answer = reply["result"].get("content") or reply["result"].get("error") or "Read finished"
                elif text.startswith("/write "):
                    path, separator, content = text[7:].partition(" | ")
                    if not separator:
                        raise ValueError("Use /write path | content")
                    action = self.terminal.propose_write(path.strip(), content)
                    answer = f"Write proposed: {action}. Review path and content, then /approve {action}"
                elif text.startswith("/approve "):
                    reply = self.terminal.approve_write(text[9:].strip())
                    answer = json.dumps(reply["result"], ensure_ascii=False)
                else:
                    answer = self.terminal.conversation(text)["reply"]
                self.completed.emit("SKULD", answer)
            except Exception as exc:
                self.completed.emit("ERROR", str(exc))

        threading.Thread(target=run, daemon=True).start()

    @Slot(str, str)
    def _finish(self, speaker, text):
        self._dialogue.append({"speaker": speaker, "text": text})
        self._busy = False
        self._refresh()

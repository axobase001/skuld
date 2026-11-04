"""D8 delivery: Notifier.deliver(item, urgency) -> {ok, error}. SMTP is the V4.0 implementation that
needs no UI. The password is read from the environment variable the config names, at send time
only, and is never stored or logged; plain-text SMTP is allowed only to a loopback host."""
import ipaddress
import os
import smtplib
import ssl
from email.message import EmailMessage

DELIVERY_KEYS = ("smtp_host", "smtp_port", "starttls", "username", "password_env", "from_addr", "timezone")
LOOPBACK_NAMES = ("localhost",)


def is_loopback(host):
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def checked_config(config):
    """Validate a `delivery` config section; -> the normalized dict (defaults filled)."""
    if not isinstance(config, dict) or set(config) - set(DELIVERY_KEYS):
        raise ValueError("delivery config keys are " + ", ".join(DELIVERY_KEYS))
    out = {"smtp_port": 587, "starttls": True, "username": None, "password_env": None, "timezone": "UTC", **config}
    if not isinstance(out.get("smtp_host"), str) or not out["smtp_host"]:
        raise ValueError("delivery needs smtp_host")
    if not isinstance(out.get("from_addr"), str) or "@" not in out["from_addr"]:
        raise ValueError("delivery needs from_addr")
    if isinstance(out["smtp_port"], bool) or not isinstance(out["smtp_port"], int) or not 0 < out["smtp_port"] < 65536:
        raise ValueError("delivery smtp_port must be a port number")
    if not out["starttls"] and not is_loopback(out["smtp_host"]):
        raise ValueError("plain-text SMTP is allowed only to a loopback host; enable starttls")
    if out["password_env"] is not None and not (isinstance(out["password_env"], str) and out["password_env"].isidentifier()):
        raise ValueError("password_env names an environment variable")
    from zoneinfo import ZoneInfo
    ZoneInfo(out["timezone"])
    return out


class Notifier:
    def deliver(self, item, urgency):
        """item = {to, subject, body}; urgency URGENT / NORMAL. -> {ok, error}."""
        raise NotImplementedError


class SmtpNotifier(Notifier):
    def __init__(self, config, timeout=15):
        self.config, self.timeout = checked_config(config), timeout

    def deliver(self, item, urgency):
        c = self.config
        msg = EmailMessage()
        msg["From"], msg["To"] = c["from_addr"], item["to"]
        msg["Subject"] = ("【紧急】" if urgency == "URGENT" else "") + item["subject"]
        msg.set_content(item["body"])
        try:
            with smtplib.SMTP(c["smtp_host"], c["smtp_port"], timeout=self.timeout) as smtp:
                if c["starttls"]:
                    smtp.starttls(context=ssl.create_default_context())
                if c["username"]:
                    password = os.environ.get(c["password_env"] or "", "")
                    if not password:
                        return {"ok": False, "error": "SMTP password environment variable is not set"}
                    smtp.login(c["username"], password)
                smtp.send_message(msg)
            return {"ok": True, "error": None}
        except (OSError, smtplib.SMTPException) as exc:
            return {"ok": False, "error": type(exc).__name__}


__all__ = ["Notifier", "SmtpNotifier", "checked_config", "is_loopback", "DELIVERY_KEYS"]

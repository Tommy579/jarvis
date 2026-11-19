"""Client e-mail SMTP pour resumes hebdomadaires."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Any


class EmailClient:
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        to_email: str | None = None,
    ) -> None:
        self.host = host or os.environ.get("SMTP_HOST", "")
        self.port = port or int(os.environ.get("SMTP_PORT", "587"))
        self.username = username or os.environ.get("SMTP_USER", "")
        self.password = password or os.environ.get("SMTP_PASSWORD", "")
        self.to_email = to_email or os.environ.get("SMTP_TO_EMAIL", "")

    def send_weekly_summary(self, subject: str, body_markdown: str) -> dict[str, Any]:
        if not (self.host and self.username and self.password and self.to_email):
            return {"ok": False, "error": "Configuration SMTP incomplete."}
        msg = EmailMessage()
        msg["From"] = self.username
        msg["To"] = self.to_email
        msg["Subject"] = subject
        msg.set_content(body_markdown)
        with smtplib.SMTP(self.host, self.port, timeout=15) as server:
            server.starttls()
            server.login(self.username, self.password)
            server.send_message(msg)
        return {"ok": True, "to": self.to_email}

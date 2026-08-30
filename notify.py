"""智能推送：重大突发事件走企微/邮件；全部写入站内 alerts。"""

from __future__ import annotations

import json
import logging
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from typing import Any
from urllib.request import Request, urlopen

from digest import load_env
from library import alerts_json

logger = logging.getLogger("notify")

BREAKING_CATS = {"geopolitics", "policy", "breaking"}
BREAKING_WORDS = (
    "hormuz",
    "attack",
    "sanction",
    "hijack",
    "strike",
    "blockade",
    "霍尔木兹",
    "袭击",
    "制裁",
    "劫持",
    "封锁",
    "停产",
)


def is_breaking(item: dict[str, Any]) -> bool:
    cats = set(item.get("categories") or [])
    if cats & BREAKING_CATS:
        blob = f"{item.get('title','')} {item.get('excerpt','')}".lower()
        if any(word in blob for word in BREAKING_WORDS):
            return True
        if "geopolitics" in cats or "breaking" in cats:
            return True
    return False


def load_alerts() -> list[dict[str, Any]]:
    path = alerts_json()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(payload, list):
        return payload
    return payload.get("alerts") or []


def save_alerts(alerts: list[dict[str, Any]]) -> None:
    path = alerts_json()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "alerts": alerts[-200:]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def format_alert(item: dict[str, Any]) -> str:
    title = item.get("title_zh") or item.get("title")
    summary = item.get("excerpt_zh") or item.get("excerpt") or ""
    analysis = item.get("analysis") or ""
    lines = [
        f"【{item.get('source') }】{title}",
        f"{item.get('published_at') or item.get('date') or ''}",
        summary,
    ]
    if analysis:
        lines.append("研判：" + analysis)
    lines.append(str(item.get("url") or ""))
    return "\n".join(lines)


def send_email(text: str, subject: str) -> bool:
    load_env()
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    to_addr = os.getenv("NOTIFY_EMAIL_TO")
    if not (host and user and password and to_addr):
        return False
    port = int(os.getenv("SMTP_PORT") or "587")
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.sendmail(user, [addr.strip() for addr in to_addr.split(",") if addr.strip()], msg.as_string())
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("邮件推送失败: %s", exc)
        return False


def send_wecom(text: str) -> bool:
    load_env()
    webhook = os.getenv("WECOM_WEBHOOK")
    if not webhook:
        return False
    body = json.dumps({"msgtype": "text", "text": {"content": text[:1800]}}, ensure_ascii=False).encode("utf-8")
    try:
        req = Request(webhook, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:  # noqa: BLE001
        logger.warning("企微推送失败: %s", exc)
        return False


def dispatch_alerts(fresh: list[dict[str, Any]], stamp: str) -> dict[str, Any]:
    breaking = [item for item in fresh if is_breaking(item)]
    alerts = load_alerts()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for item in breaking:
        alerts.append(
            {
                "at": now,
                "date": stamp,
                "title": item.get("title_zh") or item.get("title"),
                "url": item.get("url"),
                "source": item.get("source"),
                "channels": [],
            }
        )
    email_ok = False
    wecom_ok = False
    if breaking:
        text = f"{stamp} 油轮行业重大动态（{len(breaking)}）\n\n" + "\n\n".join(format_alert(i) for i in breaking[:8])
        subject = f"{stamp} 油轮行业预警 {len(breaking)} 条"
        email_ok = send_email(text, subject)
        wecom_ok = send_wecom(text)
        channels = []
        if email_ok:
            channels.append("email")
        if wecom_ok:
            channels.append("wecom")
        channels.append("in_app")
        for row in alerts[-len(breaking) :]:
            row["channels"] = channels
    save_alerts(alerts)
    return {
        "breaking": len(breaking),
        "email": email_ok,
        "wecom": wecom_ok,
        "in_app": len(breaking),
    }


def personalized_hint() -> str:
    """阅读偏好后续可接 briefs/library/readers.json；当前按关注标签过滤。"""
    load_env()
    tags = (os.getenv("NOTIFY_FOCUS_TAGS") or "地缘局势,同行动态,油轮基本面").split(",")
    return ",".join(t.strip() for t in tags if t.strip())

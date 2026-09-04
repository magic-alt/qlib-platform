from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class NotificationEnvelope:
    message_id: str
    message_kind: str
    business_date: str
    trade_date: str
    channel: str
    title: str
    summary: str
    deployment_id: str | None = None
    signal_sha256: str | None = None
    signal_date: str | None = None
    sections: Mapping[str, Any] = field(default_factory=dict)

    @property
    def payload_sha256(self) -> str:
        payload = asdict(self)
        encoded = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @property
    def idempotency_key(self) -> str:
        stable = "|".join(
            [
                self.trade_date,
                self.deployment_id or "NONE",
                self.signal_sha256 or self.payload_sha256,
                self.channel,
                self.message_kind,
            ]
        )
        return hashlib.sha256(stable.encode()).hexdigest()

    def to_feishu_card(self) -> dict[str, Any]:
        elements: list[dict[str, Any]] = [{"tag": "div", "text": {"tag": "lark_md", "content": self.summary}}]
        for heading, value in self.sections.items():
            if isinstance(value, list):
                content = "\n".join(f"- {item}" for item in value) or "- 无"
            elif isinstance(value, Mapping):
                content = "\n".join(f"- {key}: {item}" for key, item in value.items()) or "- 无"
            else:
                content = str(value)
            elements.append(
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**{heading}**\n{content}"},
                }
            )
        elements.append(
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"message_id={self.message_id} | trade_date={self.trade_date}",
                    }
                ],
            }
        )
        return {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": self.title}},
                "elements": elements,
            },
        }

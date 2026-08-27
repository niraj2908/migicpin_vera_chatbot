"""Thin typed envelopes over the raw context payloads the judge pushes.

Real payloads carry fields beyond what any brief documents (e.g. a restaurant's
customer_aggregate has different keys than a dentist's). Parsing into a rigid
schema would silently drop or reject real data, so each envelope keeps the
verbatim ``raw`` payload and exposes only the specific fields our decision and
generation logic actually reads.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CategoryContext:
    raw: dict[str, Any]

    @property
    def slug(self) -> str:
        return str(self.raw.get("slug", ""))

    @property
    def voice_tone(self) -> str:
        voice = self.raw.get("voice", {})
        return str(voice.get("tone", "")) if isinstance(voice, dict) else ""

    @property
    def vocab_allowed(self) -> list[str]:
        voice = self.raw.get("voice", {})
        allowed = voice.get("vocab_allowed", []) if isinstance(voice, dict) else []
        return [str(v) for v in allowed]

    @property
    def vocab_taboo(self) -> list[str]:
        voice = self.raw.get("voice", {})
        taboo = voice.get("vocab_taboo", []) if isinstance(voice, dict) else []
        return [str(v) for v in taboo]

    def digest_item(self, item_id: str) -> dict[str, Any] | None:
        for item in self.raw.get("digest", []):
            if isinstance(item, dict) and item.get("id") == item_id:
                return item
        return None

    def digest_items(self, kind: str | None = None) -> list[dict[str, Any]]:
        items = [item for item in self.raw.get("digest", []) if isinstance(item, dict)]
        return items if kind is None else [item for item in items if item.get("kind") == kind]


@dataclass(frozen=True)
class MerchantContext:
    raw: dict[str, Any]

    @property
    def merchant_id(self) -> str:
        return str(self.raw.get("merchant_id", ""))

    @property
    def category_slug(self) -> str:
        return str(self.raw.get("category_slug", ""))

    @property
    def name(self) -> str:
        identity = self.raw.get("identity", {})
        return str(identity.get("name", "")) if isinstance(identity, dict) else ""

    @property
    def owner_first_name(self) -> str | None:
        identity = self.raw.get("identity", {})
        name = identity.get("owner_first_name") if isinstance(identity, dict) else None
        return str(name) if name else None

    @property
    def languages(self) -> list[str]:
        identity = self.raw.get("identity", {})
        langs = identity.get("languages", []) if isinstance(identity, dict) else []
        return [str(l) for l in langs]

    @property
    def active_offers(self) -> list[dict[str, Any]]:
        offers = self.raw.get("offers", [])
        return [o for o in offers if isinstance(o, dict) and o.get("status") == "active"]

    @property
    def signals(self) -> list[str]:
        return [str(s) for s in self.raw.get("signals", [])]

    @property
    def total_active_members(self) -> int | None:
        # gyms-shaped customer_aggregate field; other categories don't carry it, hence Optional
        # rather than a default of 0 (0 would falsely claim a fact "0 active members").
        aggregate = self.raw.get("customer_aggregate", {})
        count = aggregate.get("total_active_members") if isinstance(aggregate, dict) else None
        return int(count) if isinstance(count, (int, float)) else None

    @property
    def chronic_rx_count(self) -> int | None:
        # pharmacies-shaped customer_aggregate field.
        aggregate = self.raw.get("customer_aggregate", {})
        count = aggregate.get("chronic_rx_count") if isinstance(aggregate, dict) else None
        return int(count) if isinstance(count, (int, float)) else None

    @property
    def conversation_history(self) -> list[dict[str, Any]]:
        return [h for h in self.raw.get("conversation_history", []) if isinstance(h, dict)]


@dataclass(frozen=True)
class CustomerContext:
    raw: dict[str, Any]

    @property
    def customer_id(self) -> str:
        return str(self.raw.get("customer_id", ""))

    @property
    def name(self) -> str | None:
        identity = self.raw.get("identity", {})
        name = identity.get("name") if isinstance(identity, dict) else None
        return str(name) if name else None

    @property
    def language_pref(self) -> str | None:
        identity = self.raw.get("identity", {})
        pref = identity.get("language_pref") if isinstance(identity, dict) else None
        return str(pref) if pref else None

    @property
    def state(self) -> str:
        return str(self.raw.get("state", ""))

    @property
    def consent_scope(self) -> list[str]:
        consent = self.raw.get("consent", {})
        scope = consent.get("scope", []) if isinstance(consent, dict) else []
        return [str(s) for s in scope]


@dataclass(frozen=True)
class TriggerContext:
    raw: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.raw.get("id", ""))

    @property
    def scope(self) -> str:
        return str(self.raw.get("scope", "merchant"))

    @property
    def kind(self) -> str:
        return str(self.raw.get("kind", ""))

    @property
    def merchant_id(self) -> str:
        return str(self.raw.get("merchant_id", ""))

    @property
    def customer_id(self) -> str | None:
        cid = self.raw.get("customer_id")
        return str(cid) if cid else None

    @property
    def payload(self) -> dict[str, Any]:
        payload = self.raw.get("payload", {})
        return payload if isinstance(payload, dict) else {}

    @property
    def urgency(self) -> int:
        return int(self.raw.get("urgency", 1))

    @property
    def suppression_key(self) -> str:
        return str(self.raw.get("suppression_key", ""))

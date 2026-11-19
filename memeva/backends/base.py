from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Record:
    """One retrieved memory item."""
    id: str
    content: str
    layer: str                      # e.g. tdam: L3/L2/L1/L0, agentcore: semantic/preference/summary
    score: float | None = None
    timestamp: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        ts = f" (time: {self.timestamp})" if self.timestamp else ""
        return f"[{self.layer}] {self.content}{ts}"


@dataclass
class Retrieval:
    records: list[Record]           # ordered as they should be packed into context
    seconds: float                  # wall clock of the retrieval call(s)
    calls: int                      # number of API calls made
    preamble: list[str] = field(default_factory=list)   # always-on blocks (e.g. persona) packed first


class Backend:
    name = "base"

    def ingest_user(self, user: dict, state: dict, save) -> None:
        """Write all sessions of one user in chronological order. `state` is a checkpoint dict;
        call save() after each successful write."""
        raise NotImplementedError

    def wait_ready(self, user_ids: list[str], last_write_ts: float, log) -> dict:
        raise NotImplementedError

    def retrieve(self, user_id: str, query: str) -> Retrieval:
        raise NotImplementedError

    def dump(self, user_id: str) -> list[str]:
        """Return all long-term memory text stored for a user (for leak / deletion checks)."""
        raise NotImplementedError

    def delete_user(self, user_id: str) -> dict:
        raise NotImplementedError

    def counts(self, user_id: str) -> dict:
        raise NotImplementedError


class NoMemory(Backend):
    name = "none"

    def ingest_user(self, user, state, save):
        return None

    def wait_ready(self, user_ids, last_write_ts, log):
        return {"waited": 0}

    def retrieve(self, user_id, query):
        return Retrieval([], 0.0, 0)

    def dump(self, user_id):
        return []

    def delete_user(self, user_id):
        return {}

    def counts(self, user_id):
        return {}

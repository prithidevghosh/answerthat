"""Where proposed change sets live between `POST /commands` and `POST /approve`.

A change set is not a document version — it is a proposal, and proposals are worthless
once the user has answered them. This store is process-local and deliberately so: it holds
nothing that must survive a restart, and losing it costs the user one re-issued command
rather than any part of their paper.

If that stops being true — approvals from a second process, or a proposal a user should be
able to come back to tomorrow — this becomes a table via B1's `app/core/db.py`, and the
Interface Request for it is already filed in `memory.md` §5.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

from app.agent.loop import ProposedChangeSet

DEFAULT_CAPACITY = 256
DEFAULT_TTL_SECONDS = 60 * 60 * 6


class ChangeSetNotFound(KeyError):
    """Asked for a change set that expired or never existed. Surfaced as a 404 with an
    explanation, not as an empty result the caller might read as "nothing to approve"."""


class ChangeSetStore:
    def __init__(self, capacity: int = DEFAULT_CAPACITY, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[float, ProposedChangeSet]] = OrderedDict()
        self._capacity = capacity
        self._ttl = ttl_seconds

    def put(self, change_set: ProposedChangeSet) -> ProposedChangeSet:
        with self._lock:
            self._entries[change_set.change_set_id] = (time.monotonic(), change_set)
            self._entries.move_to_end(change_set.change_set_id)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
        return change_set

    def get(self, change_set_id: str) -> ProposedChangeSet:
        with self._lock:
            entry = self._entries.get(change_set_id)
            if entry is None:
                raise ChangeSetNotFound(
                    f"change set {change_set_id!r} is not held any more. Proposals are kept for "
                    f"{self._ttl // 3600}h; re-issue the command to get a fresh one."
                )
            stored_at, change_set = entry
            if time.monotonic() - stored_at > self._ttl:
                del self._entries[change_set_id]
                raise ChangeSetNotFound(
                    f"change set {change_set_id!r} expired. Re-issue the command to get a fresh one."
                )
            return change_set

    def discard(self, change_set_id: str) -> None:
        with self._lock:
            self._entries.pop(change_set_id, None)

    def for_document(self, doc_id: str) -> list[ProposedChangeSet]:
        with self._lock:
            return [cs for _t, cs in self._entries.values() if cs.doc_id == doc_id]


__all__ = ["ChangeSetNotFound", "ChangeSetStore"]

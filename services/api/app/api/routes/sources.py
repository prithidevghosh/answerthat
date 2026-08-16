"""One source record, by id.

Every screen that shows a citation reads it from here: the parse inspector renders a
resolved reference, the review feed renders the paper behind a finding, and the edit
console renders the source a proposed citation points at. The frontend has always called
`GET /api/sources/{source_id}` — it was the one route in its client with nothing serving
it, so all three screens fetched, got a 404, and rendered the citation-shaped hole that a
`Promise.allSettled` leaves behind. A finding whose source will not load is a finding the
reader cannot check.

Reads go through the `SourceReader` port, which is `get`/`has`/`warm` and deliberately no
`put` (HR-1): nothing reachable from an HTTP handler can write to the append-only store.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import Services
from app.core.contracts import SourceRecord

router = APIRouter(prefix="/api", tags=["sources"])


def services(request: Request) -> Services:
    return request.app.state.services


@router.get("/sources/{source_id}", response_model=SourceRecord)
async def get_source(request: Request, source_id: str) -> SourceRecord:
    """The record as stored. 404 means *this store has no such source*, which is only
    answerable after warming: an unwarmed id raises from B2's store rather than reporting
    absence, precisely so "we never looked" can never be served as "does not exist"."""
    sources = services(request).require("sources")

    await sources.warm([source_id])
    record = sources.get(source_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no source record for {source_id!r}")
    return record

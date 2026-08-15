"""GROBID client.

Three things this client does that a naive one does not:

**It waits for the sidecar.** GROBID takes 30–60s to become healthy on first boot. A
connection refused in that window is not a failure, it is a container still loading CRF
models, and treating it as an error makes every cold start look broken.

**It asks for everything up front.** `consolidateHeader`, `consolidateCitations`,
`teiCoordinates` for ref/biblStruct/head/p, `includeRawCitations`, and sentence
segmentation — all in the one call. Re-running a full parse later to get coordinates
costs another 30 seconds per paper, and `includeRawCitations` in particular is not
optional for us: without it there is no verbatim raw string to quarantine (HR-3) or to
score style detection against (ADR-011).

**It distinguishes "busy" from "broken".** GROBID answers 503 when every worker is
occupied. That is backpressure, and it is retried. Anything else surfaces.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx

from app.core.config import get_settings
from app.core.errors import GrobidParseError, GrobidUnavailable

__all__ = ["GrobidClient", "GrobidOptions", "TEI_COORDINATE_ELEMENTS"]

# The frontend shows the user where each reference and heading came from on the page,
# which needs coordinates on exactly these elements. Asking later means parsing again.
TEI_COORDINATE_ELEMENTS = ("ref", "biblStruct", "head", "p")

_BUSY = 503
_NO_CONTENT = 204


@dataclass(frozen=True)
class GrobidOptions:
    """Everything we ask GROBID for, in one place so it cannot drift between callers."""

    consolidate_header: int = 1
    consolidate_citations: int = 1
    include_raw_citations: bool = True
    segment_sentences: bool = True
    coordinates: tuple[str, ...] = field(default=TEI_COORDINATE_ELEMENTS)

    def as_form(self) -> list[tuple[str, str]]:
        form: list[tuple[str, str]] = [
            ("consolidateHeader", str(self.consolidate_header)),
            ("consolidateCitations", str(self.consolidate_citations)),
            ("includeRawCitations", "1" if self.include_raw_citations else "0"),
            ("segmentSentences", "1" if self.segment_sentences else "0"),
        ]
        # teiCoordinates is a repeated field, one value per element name.
        form.extend(("teiCoordinates", element) for element in self.coordinates)
        return form


class GrobidClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        options: GrobidOptions | None = None,
        request_timeout_s: float | None = None,
        startup_timeout_s: float | None = None,
        max_busy_retries: int = 5,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.grobid_url).rstrip("/")
        self.options = options or GrobidOptions()
        self.request_timeout_s = request_timeout_s or settings.grobid_request_timeout_s
        self.startup_timeout_s = startup_timeout_s or settings.grobid_startup_timeout_s
        self.max_busy_retries = max_busy_retries

    async def is_alive(self) -> bool:
        """One probe. False for "not yet"; raises only for a genuinely broken URL."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/isalive")
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
            return False
        return response.status_code == httpx.codes.OK and "true" in response.text.lower()

    async def wait_until_healthy(self, *, poll_interval_s: float = 2.0) -> None:
        """Block until GROBID answers, or raise once the startup budget is spent.

        The budget exists so a genuinely absent sidecar still fails — waiting forever
        for a service that will never arrive is its own kind of silent failure.
        """
        deadline = time.monotonic() + self.startup_timeout_s
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            if await self.is_alive():
                return
            await asyncio.sleep(poll_interval_s)
        raise GrobidUnavailable(
            f"GROBID at {self.base_url} did not become healthy within "
            f"{self.startup_timeout_s:.0f}s ({attempts} probes). The image needs 30-60s on "
            "first boot to load its CRF models; if this persists, check "
            "`docker compose logs grobid` — the container is usually out of memory."
        )

    async def process_fulltext(self, pdf_bytes: bytes, *, filename: str = "paper.pdf") -> str:
        """Run `processFulltextDocument` and return the TEI XML.

        Raises `GrobidUnavailable` if we never got an answer, `GrobidParseError` if we
        got one we cannot use. The distinction matters: the first is retryable
        infrastructure, the second is a document we must report on honestly.
        """
        if not pdf_bytes:
            raise GrobidParseError("refusing to send an empty file to GROBID")

        url = f"{self.base_url}/api/processFulltextDocument"
        files = {"input": (filename, pdf_bytes, "application/pdf")}
        data = self.options.as_form()

        last_error: str = ""
        for attempt in range(self.max_busy_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.request_timeout_s) as client:
                    response = await client.post(url, files=files, data=data)
            except httpx.TimeoutException as exc:
                raise GrobidUnavailable(
                    f"GROBID timed out after {self.request_timeout_s:.0f}s on {filename!r}. "
                    "Long or scanned PDFs are the usual cause; raise "
                    "GROBID_REQUEST_TIMEOUT_S if this is legitimate."
                ) from exc
            except httpx.HTTPError as exc:
                raise GrobidUnavailable(f"GROBID at {self.base_url} is unreachable: {exc}") from exc

            if response.status_code == httpx.codes.OK:
                tei = response.text
                if not tei.strip():
                    raise GrobidParseError(f"GROBID returned an empty body for {filename!r}")
                if "<TEI" not in tei:
                    raise GrobidParseError(
                        f"GROBID returned a non-TEI body for {filename!r}: {tei[:200]!r}"
                    )
                return tei

            if response.status_code == _NO_CONTENT:
                # A valid PDF that GROBID could extract nothing from — a scan, most
                # likely. Reported, not silently turned into an empty document.
                raise GrobidParseError(
                    f"GROBID extracted no content from {filename!r} (HTTP 204). This is "
                    "usually a scanned PDF with no text layer; it needs OCR, which is out "
                    "of scope, and the file cannot be reviewed as-is."
                )

            if response.status_code == _BUSY:
                # Backpressure, not failure. Back off and try again.
                last_error = "all GROBID workers busy (503)"
                await asyncio.sleep(min(2.0 * (attempt + 1), 10.0))
                continue

            raise GrobidParseError(
                f"GROBID returned HTTP {response.status_code} for {filename!r}: "
                f"{response.text[:300]!r}"
            )

        raise GrobidUnavailable(
            f"GROBID stayed busy across {self.max_busy_retries + 1} attempts ({last_error}). "
            "Raise its concurrency or queue the upload; do not treat this as an unparseable "
            "document."
        )

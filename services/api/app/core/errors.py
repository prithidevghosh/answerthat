"""Canonical error types.

The four error classes named in `goal.md` Appendix A are *defined* in `contracts.py`,
because Appendix A is materialized there verbatim and byte-for-byte (CP-1 asserts this).
This module re-exports them so that `from app.core.errors import MissingAPIKeyError`
reads naturally, and adds the small number of errors the pipeline needs that Appendix A
does not name.

There is exactly one definition of each class. Never redefine one here.

HR-3: every error in this module exists so that a failure can be *named*. If you find
yourself catching one of these and returning `[]` or `None` to keep going, stop — the
correct move is to let it propagate to a defined, visible state.
"""

from app.core.contracts import (
    KernelRejection,
    MissingAPIKeyError,
    ParseFailure,
    ProviderRateLimited,
)

__all__ = [
    "MissingAPIKeyError",
    "ProviderRateLimited",
    "ParseFailure",
    "KernelRejection",
    "ConfigurationError",
    "GrobidUnavailable",
    "GrobidParseError",
    "ExportFailure",
    "StyleDetectionFailure",
    "IRVersionConflict",
    "SourceStoreViolation",
]


class ConfigurationError(RuntimeError):
    """Startup configuration is invalid in a way that is not a missing API key."""


class GrobidUnavailable(RuntimeError):
    """The GROBID sidecar did not become healthy, or dropped the connection.

    Distinct from `GrobidParseError`: this means we never got an answer, not that
    the answer was bad. GROBID takes 30-60s to boot; the client waits, and only
    raises this once the wait budget is exhausted.
    """


class GrobidParseError(ParseFailure):
    """GROBID answered, but the TEI it returned cannot be interpreted."""


class ExportFailure(RuntimeError):
    """Pandoc refused to render the document, or the round trip lost content.

    Carries Pandoc's own stderr where available — a render failure the user cannot
    diagnose is barely better than a silent one.
    """


class StyleDetectionFailure(RuntimeError):
    """Style detection could not run at all.

    Note this is *not* the same as an ambiguous or low-confidence result. Ambiguity is
    a legitimate, reportable outcome (ADR-011) carried in the result object, not an
    exception. This is raised only when the scoring itself could not be performed —
    e.g. no reference strings were extracted, or no `.csl` files are readable.
    """


class IRVersionConflict(RuntimeError):
    """An IR write targeted a version that is no longer the head of its document."""


class SourceStoreViolation(RuntimeError):
    """A non-provider module attempted to write to `source_store`. HR-1.

    This is raised by the store itself, not by convention or review. If you are seeing
    it, the fix is to route the write through a `app/providers/*` adapter backed by a
    real HTTP response — never to relax the check.
    """

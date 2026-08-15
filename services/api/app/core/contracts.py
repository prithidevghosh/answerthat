from enum import Enum
from typing import Literal, Protocol
from pydantic import BaseModel, Field

# ---------- errors ----------
class MissingAPIKeyError(RuntimeError): ...       # HR-2 — raised at startup, never caught to degrade
class ProviderRateLimited(RuntimeError): ...
class ParseFailure(RuntimeError): ...
class KernelRejection(RuntimeError): ...

# ---------- sources ----------
class AbstractSource(str, Enum):
    S2 = "s2"; OPENALEX_INVERTED = "openalex_inverted"; TLDR = "tldr"; UNAVAILABLE = "unavailable"

class Provenance(BaseModel):
    provider: Literal["semantic_scholar", "openalex", "crossref"]
    endpoint: str
    retrieved_at: str
    external_url: str                              # must be a real, resolvable URL

class SourceRecord(BaseModel):
    source_id: str
    csl: dict                                      # CSL-JSON — the one canonical citation model
    provenance: Provenance                         # HR-1: proof this came from an HTTP response
    abstract: str | None = None
    abstract_source: AbstractSource = AbstractSource.UNAVAILABLE

class SourceStore(Protocol):
    """APPEND-ONLY. Only app/providers/* may call put(). HR-1."""
    def put(self, record: SourceRecord) -> str: ...
    def get(self, source_id: str) -> SourceRecord | None: ...
    def has(self, source_id: str) -> bool: ...

# ---------- document IR ----------
class CitationAnchor(BaseModel):
    anchor_id: str
    source_ids: list[str]                          # FK into SourceStore — validated by the kernel
    offset_in_span: int
    original_marker_text: str | None = None
    provenance_kind: Literal["parsed", "agent_added"] = "parsed"
    confidence: float = 1.0
    context_fingerprint: list[float] | None = None # embedding of host sentence, for reattachment
    locator: str | None = None
    prefix: str | None = None

class Span(BaseModel):
    id: str
    text: str                                      # text lives ONLY here
    citation_anchors: list[CitationAnchor] = Field(default_factory=list)

class Block(BaseModel):
    id: str
    type: Literal["paragraph", "equation", "figure", "table", "list"]
    order: int
    spans: list[Span] = Field(default_factory=list)
    placeholder_caption: str | None = None         # figures/tables/equations: caption only (ADR-008)

class Section(BaseModel):
    id: str; level: int; title: str; order: int
    blocks: list[Block] = Field(default_factory=list)

class QuarantineEntry(BaseModel):
    raw: str
    reason: Literal["parse_failed", "unresolved", "orphan_marker", "segmentation_failed"]
    page: int | None = None

class DocumentMeta(BaseModel):
    title: str | None = None
    style_id: str | None = None
    style_confidence: float | None = None
    style_ambiguous: bool = False

class Document(BaseModel):
    doc_id: str
    version: int
    metadata: DocumentMeta
    sections: list[Section] = Field(default_factory=list)
    quarantine: list[QuarantineEntry] = Field(default_factory=list)

# ---------- parsing ----------
class ConfidenceTier(str, Enum):
    RESOLVED = "resolved"; PARSED_UNRESOLVED = "parsed_unresolved"
    LOW_CONFIDENCE = "low_confidence"; QUARANTINED = "quarantined"
    ORPHAN_MARKER = "orphan_marker"

class ParsedReference(BaseModel):
    ref_id: str
    raw_string: str                                # always retained, verbatim
    csl: dict | None
    tier: ConfidenceTier
    parse_confidence: float
    agreement_score: float | None = None           # arbiter; accept at >= 0.85
    source_id: str | None = None

# ---------- review ----------
class Claim(BaseModel):
    claim_id: str; text: str; span_id: str
    anchor_ids: list[str] = Field(default_factory=list)
    citability: float                              # streaming order = descending citability

class Candidate(BaseModel):
    source_id: str
    strategy: Literal["s2_snippet", "s2_recommendations", "openalex_search", "openalex_graph"]
    fused_score: float
    rerank_score: float | None = None

class VerificationLabel(str, Enum):
    SUPPORTS = "supports"; PARTIALLY_SUPPORTS = "partially_supports"
    DOES_NOT_ADDRESS = "does_not_address"; CONTRADICTS = "contradicts"
    UNVERIFIABLE_NO_ABSTRACT = "unverifiable_no_abstract"

class Verification(BaseModel):
    label: VerificationLabel
    quote: str | None                              # MUST be a substring of the fetched abstract
    abstract_source: AbstractSource
    confidence: float

class Finding(BaseModel):
    finding_id: str
    kind: Literal["missing_work", "claim_citation_mismatch", "no_candidates_found"]
    claim: Claim
    source_id: str | None
    verification: Verification | None
    severity: Literal["high", "medium", "low", "info"]

# ---------- agent ----------
class OperationType(str, Enum):
    ADD_CITATIONS = "AddCitations"; FIND_SUPPORT = "FindSupport"
    SHORTEN = "Shorten"; REWRITE_SECTION = "RewriteSection"
    REPLACE_CITATION = "ReplaceCitation"; MOVE_TEXT = "MoveText"
    FREEFORM_EDIT = "FreeformEdit"

class Operation(BaseModel):
    op: OperationType
    target_ids: list[str]
    params: dict = Field(default_factory=dict)
    no_typed_op_applies: bool = False              # required True for FREEFORM_EDIT
    justification: str | None = None               # required for FREEFORM_EDIT

class EditPlan(BaseModel):
    plan_id: str
    operations: list[Operation]

class ProposedChange(BaseModel):
    change_id: str
    op: Operation
    new_fragment: dict                             # partial IR
    new_source_ids: list[str] = Field(default_factory=list)
    orphaned_anchor_ids: list[str] = Field(default_factory=list)
    rationale: str

class KernelVerdict(BaseModel):
    decision: Literal["accept", "reject", "flag"]
    reasons: list[str]                             # never empty for reject/flag
    flags: list[str] = Field(default_factory=list)

# ---------- providers ----------
class Provider(Protocol):
    """Implementations MUST raise MissingAPIKeyError at construction if the key is absent. HR-2."""
    async def search_works(self, query: str, limit: int = 10) -> list[SourceRecord]: ...
    async def match_reference(self, title: str, year: int | None = None) -> SourceRecord | None: ...
    async def get_abstract(self, source_id: str) -> tuple[str | None, AbstractSource]: ...
    async def batch_hydrate(self, ids: list[str]) -> list[SourceRecord]: ...

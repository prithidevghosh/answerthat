<!-- Notice templates. Facts only, no instructions.

Each block below is one system notice, injected into the conversation when a background
job changes state. A notice states what happened and nothing else: what the agent should
*do* about a completed parse is standing policy in system.md, not a line of copy repeated
here. If a notice ever contains the words "tell the user", it has become the response
template this design exists to avoid — the model writes the sentence the researcher
reads, every time.

Blocks are delimited by `## <name>` headings and read by `prompts/__init__.py`. -->

## parse_complete

Parsing finished for {doc_id} at version {version}. {total_detected} reference(s)
detected: {resolved} resolved against an external record, {parsed_unresolved} parsed but
unresolved, {low_confidence} low confidence, {quarantined} quarantined.
{orphan_marker} orphan marker(s) were found in the text. Elapsed: {elapsed_s}s.

## parse_failed

Parsing failed for {doc_id} at stage {stage}. The reason recorded by the ingest pipeline
is: {error}

## review_complete

The review of {doc_id} finished. {findings} finding(s) emitted across {verified} of
{total} claim(s) verified. {candidates_considered} candidate(s) reached the verifier;
{quote_check_failures} were discarded because the quote could not be found in the fetched
abstract; {unverifiable_no_abstract} had no retrievable abstract;
{claims_without_candidates} claim(s) returned no candidates at all.

## review_failed

The review of {doc_id} failed and stopped. The reason recorded by the review runner is:
{error}

## budget_exhausted

This document has reached its per-document model token budget. No further model calls can
be made for {doc_id}. Nothing was truncated to fit: the work stopped rather than
silently continuing on part of the input. Reported by the token budget: {error}

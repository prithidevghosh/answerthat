"""The conversational orchestrator — the second flow through answerthat.

The deterministic flow (upload → parse → review → edit → export, one screen each, the
user driving every step) is unchanged and untouched. This package sits *above* the same
packages those screens call and drives them by tool call: the user talks to an agent, the
agent decides what to do, and the tools it calls are the same functions the screens call.

What this package is not, and the distinction is the whole design:

**It is not a state machine with a chat skin.** Nothing here matches keywords in a user
message, nothing here decides that parsing having finished means it is time to offer a
review, and nothing here holds a sentence the user will read. Routing is the model's job,
expressed as a tool call. Every sentence attributed to the agent was composed by the
model in response to facts a tool returned. When a background job changes state the
runtime injects a *system notice* — data, carrying no instructions — and runs a turn; the
model writes the message.

**Its competence comes from tools, not from prose.** If the agent cannot answer a
question about a finding, the fix is a tool that reads the finding.

Layout:

    ports.py      Protocols for everything from another package
    prompts/      the system prompt and the notice templates, as files (ADR-019)
    tools.py      the registry: schema + handler + policy per tool
    runtime.py    the agent loop and the confirmation gate
    session.py    conversation persistence (Postgres)
    watcher.py    background job → conversation event bridge
    index.py      the evidence index (embeddings + cosine lookup)

`app/orchestrator/` imports nothing from `app/parsing/`, `app/review/`, `app/agent/`,
`app/providers/`, `app/ir/` or `app/export/`. Real implementations are bound in
`app/api/deps.py`, exactly as `app/agent/` does it.
"""

from __future__ import annotations

__all__: list[str] = []

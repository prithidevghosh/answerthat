"""The agent loop, and the confirmation gate that stands between it and the manuscript.

    receive a user message
      → append it to the conversation
      → up to settings.orchestrator_max_iterations:
            converse(ORCHESTRATE, messages, tools=registry.schemas(), on_text=…)
            if the turn called tools:
                for each call: enforce policy → run → emit → append role="tool"
                continue
            else:
                emit the assistant message and end the turn

Four things this module is responsible for, in descending order of how badly they go
wrong when they are absent.

**The confirmation gate (ADR-033).** The product decision is that a chat confirmation is
enough to commit — there is no separate approval screen in this flow — which puts the
entire weight of the approval guarantee on this file. A tool marked `confirm=True` may not
execute in the same assistant turn that proposed it. The check is mechanical: the runtime
records what was last *shown* to the user, and refuses the call unless a user message
arrived after that. It is not a request in the system prompt, because a prompt-level rule
is one jailbreak away from committing an edit nobody saw.

**A tool that raises is a turn, not a crash.** The exception becomes `ok=False` with its
message, the model sees it and can explain or retry, and the loop continues. The traceback
is logged — `app/review/runner.py` learned that the hard way: a one-line user-facing string
is an honest terminal state and a useless bug report.

**The iteration cap is visible when it fires.** "I have taken N steps without finishing;
here is where I got to" is a message. A silent stop is indistinguishable from an answer.

**Every event goes to two places.** The persisted event log and the live subscriber
queues, appended under one lock, so a client that reconnects mid-turn gets the full
backlog and then live events with nothing dropped and nothing duplicated. Same design as
`ReviewJob` in `app/review/runner.py`, for the same reason.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.contracts import LLMRole
from app.core.llm import AssistantTurn, TokenBudgetExceeded
from app.ir import ids
from app.orchestrator import prompts
from app.orchestrator.ports import ConversationModel
from app.orchestrator.session import Conversation, ConversationStore
from app.orchestrator.tools import Tool, ToolContext, ToolRegistry, ToolResult, build_registry

log = logging.getLogger("app.orchestrator.runtime")

__all__ = ["Orchestrator", "TurnState"]


@dataclass
class TurnState:
    """Per-conversation state that outlives a single turn.

    Deliberately small, and deliberately *not* a workflow. Nothing here decides what
    happens next; it records what the user has been shown, which is the only thing the
    confirmation gate needs to know.
    """

    shown: dict[str, int] = field(default_factory=dict)
    """`proposal key → index in the message log at which it was put in front of the user`.

    Keyed by the specific thing being proposed — `change_set:cs-123`, not "an edit" — so
    that answering yes to one proposal cannot authorise a different one. A model that
    proposes change set A, is told yes, and then commits change set B is not executing the
    user's decision, and the key mismatch is what catches it.
    """
    last_proposal_id: str | None = None
    last_proposal_kind: str | None = None
    """The most recent proposal, for the refusal message only. Never for the decision."""
    last_user_message_at: int = -1
    """Index of the most recent user message. -1 means the user has not spoken yet."""
    running: asyncio.Task | None = field(default=None, repr=False)

    def answered(self, keys: list[str]) -> bool:
        """Has the user replied since any of these proposals was shown?

        Absence of a key is a refusal, not a pass. The very first user message in a
        conversation must not authorise a commit — "the user has spoken at some point" is
        not the same claim as "the user was shown this and answered", and conflating them
        is how a commit lands on a change set nobody ever saw.
        """
        return any(
            key in self.shown and self.last_user_message_at > self.shown[key] for key in keys
        )

    def show(self, key: str, at: int, *, kind: str | None = None, proposal_id: str | None = None) -> None:
        self.shown[key] = at
        if kind is not None:
            self.last_proposal_kind = kind
        if proposal_id is not None:
            self.last_proposal_id = proposal_id


def confirmation_keys(tool_name: str, arguments: dict) -> list[str]:
    """The proposals that would authorise this call.

    Two shapes. `commit_change_set` and `export_latex` have a tool that produces a real
    proposal — a change set, a manifest — and the key names it. `revert_document` has no
    such tool: the agent names a version out of the version list, so the only thing that
    can stand as "this was put in front of the user" is the runtime's own refusal of an
    earlier attempt. That is why `refused:` is a key rather than a special case: the first
    call is refused with instructions to present and ask, and the user's answer to *that*
    is the confirmation.
    """
    if tool_name == "commit_change_set":
        return [f"change_set:{arguments.get('change_set_id')}", f"refused:commit_change_set:{arguments.get('change_set_id')}"]
    if tool_name == "export_latex":
        return [f"export:{arguments.get('doc_id')}", f"refused:export_latex:{arguments.get('doc_id')}"]
    return [f"refused:{tool_name}:{arguments.get('doc_id') or ''}"]


class Orchestrator:
    """Runs conversations. One instance per process; state is per conversation."""

    def __init__(
        self,
        *,
        model: ConversationModel,
        conversations: ConversationStore,
        tool_context: ToolContext,
        settings: Any,
    ) -> None:
        self._model = model
        self._conversations = conversations
        self._ctx = tool_context
        self._settings = settings
        self._state: dict[str, TurnState] = {}
        self._registries: dict[str, ToolRegistry] = {}

    # ------------------------------------------------------------------ accessors

    @property
    def conversations(self) -> ConversationStore:
        return self._conversations

    def state_for(self, conversation_id: str) -> TurnState:
        state = self._state.get(conversation_id)
        if state is None:
            state = self._state[conversation_id] = TurnState()
        return state

    def registry_for(self, conversation: Conversation) -> ToolRegistry:
        registry = self._registries.get(conversation.conversation_id)
        if registry is None:
            registry = self._registries[conversation.conversation_id] = build_registry(
                self._ctx, conversation.doc_id
            )
        return registry

    # ------------------------------------------------------------------ turns

    async def send_user_message(self, conversation: Conversation, text: str) -> None:
        """Record a user message and run a turn in the background."""
        state = self.state_for(conversation.conversation_id)
        message = await self._conversations.append_message(conversation, "user", text)
        # The one place `last_user_message_at` moves. A system notice does not move it:
        # a parse finishing is not the user answering a question about an edit, and if it
        # counted, a background job could authorise a commit.
        state.last_user_message_at = message.seq
        await self._conversations.append_event(
            conversation,
            "message",
            {"message_id": message.message_id, "role": "user", "content": text},
        )
        await self.run_turn(conversation)

    async def notify(self, conversation: Conversation, notice_name: str, **facts: Any) -> None:
        """Inject a system notice and run a turn.

        The notice is data. The message the researcher reads is written by the model, in
        response to it. This is the whole of the "no hardcoded agent copy" rule: there is
        no branch here that composes a sentence about a finished parse.
        """
        content = prompts.notice(notice_name, **facts)
        message = await self._conversations.append_message(conversation, "system_notice", content)
        await self._conversations.append_event(
            conversation,
            "message",
            {"message_id": message.message_id, "role": "system_notice", "content": content},
        )
        await self.run_turn(conversation)

    async def run_turn(self, conversation: Conversation) -> None:
        """Start a turn in the background, cancelling nothing that is already running."""
        state = self.state_for(conversation.conversation_id)
        if state.running is not None and not state.running.done():
            # Queued behind the running turn rather than run beside it: two concurrent
            # turns would interleave their deltas into one transcript and could both
            # decide to call the same mutating tool.
            previous = state.running

            async def _after() -> None:
                # A cancelled predecessor is the `stop` path and is not this turn's
                # problem: the user interrupted the previous answer and then said
                # something else, and the new message still deserves a reply.
                with contextlib.suppress(asyncio.CancelledError):
                    await previous
                await self._run_turn(conversation)

            state.running = asyncio.create_task(_after())
            return
        state.running = asyncio.create_task(self._run_turn(conversation))

    async def stop(self, conversation: Conversation) -> bool:
        """Cancel the in-flight turn. The conversation stays usable."""
        state = self.state_for(conversation.conversation_id)
        task = state.running
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def _run_turn(self, conversation: Conversation) -> None:
        try:
            await self._turn(conversation)
        except asyncio.CancelledError:
            await self._conversations.append_event(
                conversation,
                "error",
                {
                    "error": "interrupted",
                    "detail": (
                        "You stopped this turn. Nothing that had not already been written was "
                        "written, and the conversation is still usable."
                    ),
                },
            )
            raise
        except TokenBudgetExceeded as exc:
            # Reaches the user as a visible chat error, never as a conversation that
            # quietly stops mid-sentence. ADR-015: nothing was truncated to fit.
            log.warning("token budget exhausted for %s: %s", conversation.doc_id, exc)
            await self._conversations.append_event(
                conversation,
                "error",
                {"error": "token_budget_exceeded", "detail": str(exc)},
            )
        except Exception as exc:  # noqa: BLE001 — reported to the client, logged in full
            log.exception("orchestrator turn failed for conversation %s", conversation.conversation_id)
            await self._conversations.append_event(
                conversation,
                "error",
                {"error": type(exc).__name__, "detail": str(exc)},
            )

    async def _turn(self, conversation: Conversation) -> None:
        registry = self.registry_for(conversation)
        state = self.state_for(conversation.conversation_id)
        system = await self._system_prompt(conversation)
        max_iterations = self._settings.orchestrator_max_iterations

        for _iteration in range(max_iterations):
            messages = self._trim(conversation, system)
            message_id = ids.new_id("msg")
            await self._conversations.append_event(
                conversation, "message_start", {"message_id": message_id, "role": "assistant"}
            )

            async def emit_delta(text: str, _mid: str = message_id) -> None:
                await self._conversations.append_event(
                    conversation, "message_delta", {"message_id": _mid, "text": text}
                )

            turn: AssistantTurn = await self._model.converse(
                LLMRole.ORCHESTRATE,
                messages,
                tools=registry.schemas(),
                system=system,
                doc_id=conversation.doc_id,
                on_text=emit_delta,
            )

            await self._conversations.append_message(
                conversation,
                "assistant",
                turn.text,
                tool_calls=[
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in turn.tool_calls
                ]
                or None,
                message_id=message_id,
            )
            await self._conversations.append_event(
                conversation,
                "message",
                {"message_id": message_id, "role": "assistant", "content": turn.text},
            )

            if not turn.tool_calls:
                await self._conversations.append_event(
                    conversation,
                    "done",
                    {
                        "message_id": message_id,
                        "tokens_used": turn.tokens,
                        "budget_remaining": self._budget_remaining(conversation.doc_id),
                    },
                )
                return

            await self._run_tool_calls(conversation, registry, state, turn)

        # The cap fired. A visible message, not a silent stop: a conversation that just
        # ends is indistinguishable from one that finished.
        detail = (
            f"I took {max_iterations} steps on this without reaching an answer, so I have "
            "stopped. Nothing further was run. What is above is where I got to."
        )
        message = await self._conversations.append_message(conversation, "assistant", detail)
        await self._conversations.append_event(
            conversation,
            "message",
            {"message_id": message.message_id, "role": "assistant", "content": detail},
        )
        await self._conversations.append_event(
            conversation,
            "error",
            {"error": "iteration_cap_reached", "detail": detail},
        )

    # ------------------------------------------------------------------ tool calls

    async def _run_tool_calls(
        self,
        conversation: Conversation,
        registry: ToolRegistry,
        state: TurnState,
        turn: AssistantTurn,
    ) -> None:
        """Execute this turn's tool calls and append their results.

        Independent calls run concurrently — a turn that reads the parse report, the
        review status and the outline should cost one round trip, not three. Mutating and
        confirmable calls are serialised before the rest: two commits racing each other
        against the same head would produce a version conflict that neither the model nor
        the user caused.
        """
        # The gate is decided for every call **before any of them runs**, against the state
        # as it was when the turn began. Deciding it during execution would let a proposal
        # made earlier in this same turn authorise a commit later in it — which is exactly
        # the "propose and commit in one breath" case, arriving through the back door of
        # execution order rather than through the rule.
        authorised = {
            call.call_id: state.answered(confirmation_keys(call.name, call.arguments))
            for call in turn.tool_calls
        }

        gated = [call for call in turn.tool_calls if self._is_serial(registry, call.name)]
        parallel = [call for call in turn.tool_calls if not self._is_serial(registry, call.name)]

        results: dict[str, ToolResult] = {}
        for call in gated:
            results[call.call_id] = await self._invoke(
                conversation, registry, state, call, authorised[call.call_id]
            )
        if parallel:
            outcomes = await asyncio.gather(
                *(
                    self._invoke(conversation, registry, state, call, authorised[call.call_id])
                    for call in parallel
                )
            )
            for call, outcome in zip(parallel, outcomes, strict=True):
                results[call.call_id] = outcome

        for call in turn.tool_calls:
            result = results[call.call_id]
            await self._conversations.append_message(
                conversation, "tool", result.for_model(), tool_call_id=call.call_id
            )

    @staticmethod
    def _is_serial(registry: ToolRegistry, name: str) -> bool:
        tool = registry.get(name)
        return bool(tool and (tool.mutating or tool.confirm))

    async def _invoke(
        self,
        conversation: Conversation,
        registry: ToolRegistry,
        state: TurnState,
        call: Any,
        authorised: bool,
    ) -> ToolResult:
        tool = registry.get(call.name)
        label = tool.label if tool else call.name
        await self._conversations.append_event(
            conversation,
            "tool_call",
            {
                "call_id": call.call_id,
                "name": call.name,
                "arguments": call.arguments,
                "label": label,
            },
        )

        if tool is None:
            result = ToolResult.failed(
                f"there is no tool named {call.name!r}. Available: {', '.join(registry.names())}."
            )
        elif tool.confirm and not authorised:
            result = self._refuse_unconfirmed(tool, state)
            # The refusal itself is what the user is shown, so it becomes the proposal for
            # everything that has no proposing tool of its own (`revert_document`). The
            # agent presents it, the user answers, and the next attempt is authorised.
            for key in confirmation_keys(call.name, call.arguments):
                if key.startswith("refused:"):
                    state.show(key, len(conversation.messages))
        else:
            result = await self._execute(tool, call)
            await self._note_proposal(conversation, state, tool, call, result)

        await self._conversations.append_event(
            conversation, "tool_result", {"call_id": call.call_id, "name": call.name, **result.as_event()}
        )
        return result

    @staticmethod
    def _refuse_unconfirmed(tool: Tool, state: TurnState) -> ToolResult:
        """The gate. Mechanical, and it does not consult the model's opinion.

        The condition is not "did the model say it asked" — it is "did a user message
        arrive after the proposal was shown". Anything softer is satisfied by a model that
        writes 'Shall I commit? Yes, committing.' in one turn.
        """
        return ToolResult.failed(
            f"{tool.name} was refused: it changes the document or produces a file, and it "
            "cannot run in the same turn that proposed it. "
            + (
                "Present the proposal you have — the changes, the citation ledger, and every "
                "orphaned citation anchor with its own keep/move/remove choice — and wait for "
                "the user to answer. Then call it again."
                if state.last_proposal_id
                else "Produce the proposal first (propose_edit, get_export_manifest, or the "
                "version list), show it to the user, and wait for their answer."
            ),
            {
                "kind": "confirmation_required",
                "tool": tool.name,
                "last_proposal_id": state.last_proposal_id,
                "last_proposal_kind": state.last_proposal_kind,
            },
        )

    async def _execute(self, tool: Tool, call: Any) -> ToolResult:
        try:
            return await tool.handler(**call.arguments)
        except TypeError as exc:
            # A bad argument list is the model's mistake and it can fix it — but only if
            # it is told, so this is a tool result rather than a dead turn.
            log.exception("tool %s rejected its arguments", tool.name)
            return ToolResult.failed(
                f"{tool.name} could not be called with those arguments: {exc}"
            )
        except TokenBudgetExceeded:
            # Not caught here. A spent budget is a property of the conversation, not a
            # failure of this tool, and it has to stop the turn rather than become one
            # `ok=False` the model retries around.
            raise
        except Exception as exc:  # noqa: BLE001 — becomes a tool result the model sees
            log.exception("tool %s raised", tool.name)
            return ToolResult.failed(f"{tool.name} failed: {type(exc).__name__}: {exc}")

    async def _note_proposal(
        self,
        conversation: Conversation,
        state: TurnState,
        tool: Tool,
        call: Any,
        result: ToolResult,
    ) -> None:
        """Record that something the user must confirm has been put in front of them.

        The `awaiting_confirmation` event carries the *structured* proposal, so the UI
        renders the real diff, the real ledger and the real orphan list rather than the
        agent's summary of them. The agent's prose and the thing being approved are
        different artefacts and the user approves the second one.
        """
        if tool.confirm:
            # A confirmable tool that actually ran consumes its authorisation. Without
            # this, one "yes" would license every later call naming the same change set —
            # a second commit, or a second export — with no further word from the user.
            for key in confirmation_keys(tool.name, call.arguments):
                state.shown.pop(key, None)
            return

        if not result.ok:
            return

        kind = {"propose_edit": "change_set", "get_export_manifest": "export"}.get(tool.name)
        if kind is None:
            return

        if kind == "change_set":
            if result.data.get("status") == "failed":
                # A change set the kernel refused outright is not a proposal. Recording it
                # as one would let "propose (failed) → user says anything → commit" pass
                # the gate on an edit that never described a valid change.
                return
            proposal_id = result.data.get("change_set_id")
            key = f"change_set:{proposal_id}"
        else:
            proposal_id = call.arguments.get("doc_id")
            key = f"export:{proposal_id}"

        state.show(key, len(conversation.messages), kind=kind, proposal_id=proposal_id)
        await self._conversations.append_event(
            conversation,
            "awaiting_confirmation",
            {"kind": kind, "proposal": result.data},
        )

    # ------------------------------------------------------------------ context

    async def _system_prompt(self, conversation: Conversation) -> str:
        title = None
        document = await self._ctx.documents.get(conversation.doc_id)
        if document is None:
            draft = self._ctx.ingest.draft_document(conversation.doc_id)
            title = draft.metadata.title if draft is not None else None
        else:
            title = document.metadata.title
        return prompts.system_prompt(doc_id=conversation.doc_id, title=title)

    def _budget_remaining(self, doc_id: str) -> int | None:
        budget = getattr(self._model, "budget", None)
        if budget is None:
            return None
        try:
            return max(0, budget.limit - budget.spent(doc_id))
        except Exception:  # noqa: BLE001 — a diagnostic, never worth failing a turn for
            return None

    def _trim(self, conversation: Conversation, system: str) -> list[dict]:
        """The message list for this call, trimmed to the context budget.

        **Only old tool results are dropped.** Never a user message — that is the record
        of what was actually asked — and never the system prompt. A dropped tool result
        costs the model a re-fetch it can perform; a dropped user message costs the
        conversation its subject.

        A dropped result is replaced by a stub rather than removed outright, because the
        `role="tool"` message must still answer the `tool_calls` entry that requested it:
        an assistant message referencing a tool call with no matching result is a
        malformed conversation and OpenAI rejects it.

        The estimate is characters over `orchestrator_chars_per_token`, not a tokenizer.
        A per-turn tokenizer dependency to be 15% more accurate about a budget that is
        already set with slack is not worth having.
        """
        budget = self._settings.orchestrator_context_budget_tokens
        per_token = self._settings.orchestrator_chars_per_token
        messages = conversation.openai_messages()

        def cost(items: list[dict]) -> int:
            return (len(system) + sum(len(json.dumps(item, default=str)) for item in items)) // per_token

        if cost(messages) <= budget:
            return messages

        trimmed = [dict(message) for message in messages]
        dropped = 0
        for index, (message, original) in enumerate(zip(trimmed, conversation.messages, strict=True)):
            if cost(trimmed) <= budget:
                break
            if original.role != "tool":
                continue
            # The most recent results are the ones the current turn is reasoning over, so
            # trimming runs oldest-first and stops as soon as it is under budget.
            if index >= len(trimmed) - 6:
                break
            message["content"] = (
                "[earlier tool result dropped to stay within the context budget — call the "
                "tool again if you need it]"
            )
            dropped += 1

        if dropped:
            log.info(
                "conversation %s: dropped %d old tool result(s) to fit the %d-token context "
                "budget; no user message and no system prompt was touched",
                conversation.conversation_id,
                dropped,
                budget,
            )
        return trimmed

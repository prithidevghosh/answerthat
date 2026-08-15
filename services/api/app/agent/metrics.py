"""Operation telemetry — chiefly the `FreeformEdit` firing rate (ADR-009).

ADR-009 names the real risk of an escape hatch: planner laziness. An operation that
always applies attracts every command, and `Shorten` quietly becomes dead code. The
tripwire is ~20% of commands; above that **the typed vocabulary is wrong and the fix is a
better vocabulary, not more reliance on the hatch**.

A tripwire nobody can see is not a tripwire, so the rate is exposed on the API
(`GET /api/agent/metrics`) rather than buried in a log line.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter

from pydantic import BaseModel

from app.core.contracts import EditPlan, OperationType

log = logging.getLogger("app.agent.metrics")

FREEFORM_TRIPWIRE = 0.20


class OperationMetrics(BaseModel):
    commands: int
    operations: int
    by_operation: dict[str, int]
    freeform_operations: int
    freeform_rate: float
    """FreeformEdit as a fraction of all planned operations."""
    freeform_commands: int
    freeform_command_rate: float
    """Commands whose plan used the hatch at all, as a fraction of all commands. This is
    the number ADR-009's ~20% refers to."""
    tripwire: float = FREEFORM_TRIPWIRE
    tripped: bool
    justifications: list[str]


class MetricsRegistry:
    """Process-local counters. Small, thread-safe, and deliberately not a dependency of
    anything that decides whether an edit is legal."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_operation: Counter[str] = Counter()
        self._commands = 0
        self._freeform_commands = 0
        self._justifications: list[str] = []

    def record_plan(self, plan: EditPlan) -> None:
        with self._lock:
            self._commands += 1
            used_hatch = False
            for operation in plan.operations:
                self._by_operation[operation.op.value] += 1
                if operation.op is OperationType.FREEFORM_EDIT:
                    used_hatch = True
                    if operation.justification:
                        self._justifications.append(operation.justification)
            if used_hatch:
                self._freeform_commands += 1
            snapshot = self._snapshot_locked()

        if snapshot.tripped:
            log.warning(
                "FreeformEdit fired on %.0f%% of commands (%d/%d), above the ADR-009 tripwire of "
                "%.0f%%. The typed vocabulary is wrong — fix the vocabulary, do not lean on the "
                "hatch. Recent justifications: %s",
                snapshot.freeform_command_rate * 100,
                snapshot.freeform_commands,
                snapshot.commands,
                FREEFORM_TRIPWIRE * 100,
                snapshot.justifications[-3:],
            )
        else:
            log.info(
                "plan recorded: %s (freeform rate %.2f over %d commands)",
                dict(self._by_operation),
                snapshot.freeform_command_rate,
                snapshot.commands,
            )

    def snapshot(self) -> OperationMetrics:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> OperationMetrics:
        total_operations = sum(self._by_operation.values())
        freeform_operations = self._by_operation.get(OperationType.FREEFORM_EDIT.value, 0)
        command_rate = (self._freeform_commands / self._commands) if self._commands else 0.0
        return OperationMetrics(
            commands=self._commands,
            operations=total_operations,
            by_operation=dict(self._by_operation),
            freeform_operations=freeform_operations,
            freeform_commands=self._freeform_commands,
            freeform_rate=(freeform_operations / total_operations) if total_operations else 0.0,
            freeform_command_rate=command_rate,
            tripped=self._commands >= 5 and command_rate > FREEFORM_TRIPWIRE,
            justifications=list(self._justifications),
        )


METRICS = MetricsRegistry()

__all__ = ["FREEFORM_TRIPWIRE", "METRICS", "MetricsRegistry", "OperationMetrics"]

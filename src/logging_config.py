"""Trace-aware logging: injects a per-order correlation ID into log records.

Uses contextvars so the trace flows through the synchronous call stack without
threading issues and without threading concerns for future async use.
"""

import logging
from contextvars import ContextVar

# Holds the current order ID during pipeline processing.
# Set by process_order_node before invoking the pipeline, cleared after.
_current_trace_id: ContextVar[str | None] = ContextVar(
    "current_trace_id", default=None
)


def set_trace_id(order_id: str) -> None:
    """Set the trace ID for the current context (order being processed)."""
    _current_trace_id.set(order_id)


def clear_trace_id() -> None:
    """Clear the trace ID after processing completes."""
    _current_trace_id.set(None)


class TraceFilter(logging.Filter):
    """Injects the current trace ID into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = _current_trace_id.get()
        record.trace_id = trace_id if trace_id else "-"
        return True

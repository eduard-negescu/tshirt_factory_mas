---
name: logging-trace-contextvars
description: Inject per-request trace/correlation IDs into Python log records using contextvars + a logging.Filter — no need to pass logger instances around, works with synchronous code and future-proof for async.
source: auto-skill
extracted_at: '2026-06-04T13:01:00.913Z'
---

# Per-Request Trace IDs in Python Logging via contextvars

Use this when you need a trace/correlation ID that appears in every log line for the duration of a logical operation (e.g., processing an order, handling a web request), without threading it through every function signature.

## Why contextvars

- Unlike `threading.local`, `contextvars` works correctly with async code (asyncio tasks each get their own context copy).
- Unlike passing a logger adapter everywhere, it requires zero changes to existing function signatures.
- The trace is set once at the entry point and cleared in a `finally` block — clean separation of concerns.

## Step 1 — Create the trace context module

```python
# logging_config.py
import logging
from contextvars import ContextVar

_current_trace_id: ContextVar[str | None] = ContextVar(
    "current_trace_id", default=None
)

def set_trace_id(trace_id: str) -> None:
    _current_trace_id.set(trace_id)

def clear_trace_id() -> None:
    _current_trace_id.set(None)

class TraceFilter(logging.Filter):
    """Injects the current trace ID into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = _current_trace_id.get()
        record.trace_id = trace_id if trace_id else "-"
        return True
```

## Step 2 — Wire the filter into logging setup

```python
def setup_logging():
    trace_filter = TraceFilter()

    handler = logging.StreamHandler(...)
    handler.addFilter(trace_filter)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(trace_id)-8s | %(name)s | %(message)s"
    ))
```

The filter must be added to every handler. The formatter uses `%(trace_id)s` — the filter sets `record.trace_id` so the formatter can reference it.

## Step 3 — Set and clear the trace at boundaries

```python
def process_order(order_id: str):
    set_trace_id(order_id)
    try:
        # All log calls in this block (and any functions called from it)
        # automatically get trace_id = order_id
        do_work()
    finally:
        clear_trace_id()
```

The `finally` block ensures the trace is always cleared, even if an exception propagates out.

## Choosing trace ID values

- **Per-order processing**: use the order ID directly (e.g., `"O-001"`)
- **Batch operations** (scheduling, planning): use a descriptive label like `"[plan]"` or `"[replan-O-001]"`
- **No trace context**: defaults to `"-"` — makes it visually obvious when a log line has no trace

## Verification

Test with:

```python
set_trace_id("O-001")
logger.info("processing")
clear_trace_id()
logger.info("done")
```

Expected output:

```
INFO     | O-001    | ... | processing
INFO     | -        | ... | done
```

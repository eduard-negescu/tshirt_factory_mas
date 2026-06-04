"""FastAPI server for the T-shirt Factory MAS web UI.

Routes:
  GET  /                    Landing page (order form)
  GET  /live                Live factory view with Canvas + SSE
  GET  /history             Past runs list from Postgres checkpoints
  GET  /replay/{thread_id}  Replay viewer
  GET  /stream              SSE endpoint for live run
  GET  /stream/{thread_id}  SSE endpoint for replay
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from langgraph.checkpoint.postgres import PostgresSaver

from config.settings import Settings
from graph.state import SimulationState
from ui.factory import create_simulation

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="T-Shirt Factory MAS")

# ---------------------------------------------------------------------------
# Static helpers
# ---------------------------------------------------------------------------


def _read_static(filename: str) -> str:
    return (STATIC_DIR / filename).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def landing():
    return _read_static("index.html")


@app.get("/live", response_class=HTMLResponse)
async def live_page():
    return _read_static("live.html")


@app.get("/history", response_class=HTMLResponse)
async def history_page():
    return _read_static("history.html")


@app.get("/replay/{thread_id}", response_class=HTMLResponse)
async def replay_page():
    return _read_static("replay.html")


# ---------------------------------------------------------------------------
# SSE: live run
# ---------------------------------------------------------------------------


@app.get("/stream")
async def stream_live(
    request: Request,
    order_count: int = Query(10, ge=1, le=50),
    urgent_ratio: float = Query(0.3, ge=0.0, le=1.0),
):
    """SSE endpoint that runs a new simulation and streams events."""
    settings = Settings()
    thread_id = f"sim-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    event_queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def event_callback(event: dict) -> None:
        """Called from within graph.stream() thread — bridge to async queue."""
        loop.call_soon_threadsafe(event_queue.put_nowait, event)

    async def event_generator():
        with PostgresSaver.from_conn_string(settings.database_url) as checkpointer:
            checkpointer.setup()
            graph, initial_state, config = create_simulation(
                checkpointer=checkpointer,
                settings=settings,
                order_count=order_count,
                urgent_ratio=urgent_ratio,
                thread_id=thread_id,
                event_callback=event_callback,
            )

            def sync_runner() -> None:
                """Run graph.stream() in a thread, pushing chunks to the queue."""
                try:
                    for chunk in graph.stream(
                        initial_state, config, stream_mode="updates"
                    ):
                        if "plan" in chunk:
                            plan = chunk["plan"]
                            loop.call_soon_threadsafe(
                                event_queue.put_nowait,
                                {
                                    "type": "plan",
                                    "queue": plan.get("queue", []),
                                    "reason": plan.get("schedule_reason", ""),
                                    "re_plan_count": plan.get("re_plan_count", 0),
                                },
                            )
                        elif "process_order" in chunk:
                            po = chunk["process_order"]
                            result = po.get("pipeline_result", "")
                            loop.call_soon_threadsafe(
                                event_queue.put_nowait,
                                {
                                    "type": "stats",
                                    "iteration": po.get("iteration", 0),
                                    "completed": po.get("completed_count", 0),
                                    "re_plan_count": po.get("re_plan_count", 0),
                                    "pipeline_result": result,
                                },
                            )
                    loop.call_soon_threadsafe(
                        event_queue.put_nowait,
                        {"type": "done", "thread_id": thread_id},
                    )
                except Exception as exc:
                    logger.exception("Graph execution failed")
                    loop.call_soon_threadsafe(
                        event_queue.put_nowait,
                        {"type": "error", "message": str(exc) or type(exc).__name__},
                    )

            graph_task = asyncio.create_task(asyncio.to_thread(sync_runner))

            while True:
                event = await event_queue.get()
                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                if event["type"] in ("done", "error"):
                    break

            await graph_task

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# SSE: replay from checkpoints
# ---------------------------------------------------------------------------


@app.get("/stream/{thread_id}")
async def stream_replay(request: Request, thread_id: str):
    """SSE endpoint that replays a past run from Postgres checkpoints."""
    settings = Settings()
    event_queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def collect_and_emit() -> None:
        """Run in thread: collect snapshots via sync get_state_history, emit in order."""
        try:
            with PostgresSaver.from_conn_string(settings.database_url) as checkpointer:
                checkpointer.setup()
                graph, _, _ = create_simulation(
                    checkpointer=checkpointer,
                    settings=settings,
                    order_count=0,
                    thread_id=thread_id,
                )
                config = {"configurable": {"thread_id": thread_id}}

                # Collect all snapshots (returns reverse chronological)
                snapshots: list[SimulationState] = []
                for snapshot in graph.get_state_history(config):
                    snapshots.append(SimulationState(**snapshot.values))

            if not snapshots:
                loop.call_soon_threadsafe(
                    event_queue.put_nowait,
                    {"type": "error", "message": "No checkpoints found for this run"},
                )
                return

            # Reverse to chronological
            snapshots.reverse()

            # Diff and emit events
            last_queue: list[str] = []
            last_re_plan: int = -1
            last_iteration: int = -1
            prev_completed: set[str] = set()

            for state in snapshots:
                if state.queue != last_queue or state.re_plan_count != last_re_plan:
                    loop.call_soon_threadsafe(
                        event_queue.put_nowait,
                        {
                            "type": "plan",
                            "queue": list(state.queue),
                            "reason": state.schedule_reason or "(replay)",
                            "re_plan_count": state.re_plan_count,
                        },
                    )
                    last_queue = list(state.queue)
                    last_re_plan = state.re_plan_count

                if state.iteration != last_iteration:
                    loop.call_soon_threadsafe(
                        event_queue.put_nowait,
                        {
                            "type": "stats",
                            "iteration": state.iteration,
                            "completed": state.completed_count,
                            "re_plan_count": state.re_plan_count,
                            "pipeline_result": state.pipeline_result,
                        },
                    )
                    last_iteration = state.iteration

                current_completed = set(state.completed_orders.keys())
                for oid in current_completed - prev_completed:
                    order = state.all_orders.get(oid)
                    loop.call_soon_threadsafe(
                        event_queue.put_nowait,
                        {
                            "type": "order_complete",
                            "order_id": oid,
                            "design_name": order.design_name if order else "unknown",
                        },
                    )
                prev_completed = current_completed

                # Small delay to simulate real-time replay
                import time
                time.sleep(0.1)

            loop.call_soon_threadsafe(
                event_queue.put_nowait,
                {"type": "done", "thread_id": thread_id},
            )

        except Exception as exc:
            logger.exception("Replay failed for %s", thread_id)
            loop.call_soon_threadsafe(
                event_queue.put_nowait,
                {"type": "error", "message": str(exc) or type(exc).__name__},
            )

    async def _forward():
        graph_task = asyncio.create_task(asyncio.to_thread(collect_and_emit))
        while True:
            event = await event_queue.get()
            yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
            if event["type"] in ("done", "error"):
                break
        await graph_task

    return StreamingResponse(
        _forward(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# History data (HTML fragment for HTMX)
# ---------------------------------------------------------------------------


@app.get("/api/history")
async def api_history():
    """Return list of past runs as HTML for HTMX swap."""
    settings = Settings()

    try:
        import psycopg

        conn = psycopg.connect(settings.database_url)
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT thread_id FROM checkpoints "
            "ORDER BY thread_id DESC LIMIT 50"
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return HTMLResponse(
                '<li class="empty">No past runs found. Run a simulation first!</li>'
            )

        items = []
        for row in rows:
            tid = row[0]
            items.append(
                f'<li><a href="/replay/{tid}">'
                f'<span class="run-id">{tid}</span></a></li>'
            )
        return HTMLResponse("".join(items))
    except Exception as exc:
        logger.warning("Failed to query history: %s", exc)
        return HTMLResponse(
            '<li class="empty">Could not load runs (is PostgreSQL running?)</li>'
        )


# ---------------------------------------------------------------------------
# Static file serving (SVGs, JS)
# ---------------------------------------------------------------------------


@app.get("/static/{filename:path}")
async def serve_static(filename: str):
    """Serve static files (SVGs, JS) with correct MIME types."""
    file_path = STATIC_DIR / filename
    if not file_path.exists():
        return HTMLResponse("Not found", status_code=404)

    content = file_path.read_bytes()
    suffix = file_path.suffix.lower()

    media_types = {
        ".svg": "image/svg+xml",
        ".js": "application/javascript",
        ".css": "text/css",
        ".html": "text/html",
    }
    media_type = media_types.get(suffix, "application/octet-stream")

    return Response(content=content, media_type=media_type)

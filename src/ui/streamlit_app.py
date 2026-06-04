"""Streamlit UI for T-Shirt Factory MAS — live simulation dashboard."""

import sys
from datetime import datetime
from pathlib import Path

# Ensure src/ is on sys.path so imports work both via `streamlit run` and `uv run ui`
_src = Path(__file__).resolve().parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import streamlit as st

from bus import MessageBus
from config.settings import Settings
from graph.builder import build_graph
from graph.state import SimulationState
from langgraph.checkpoint.postgres import PostgresSaver
from ui.factory import (
    create_agents,
    create_chains,
    create_config,
    create_equipment,
    generate_orders,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="T-Shirt Factory MAS",
    page_icon="🏭",
    layout="wide",
)

st.title("🏭 T-Shirt Factory MAS")
st.caption("Multi-Agent Simulation — LangGraph + Ollama")

# ---------------------------------------------------------------------------
# Sidebar — configuration
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Configuration")
    num_orders = st.slider("Number of orders", 1, 30, 10)
    urgent_ratio = st.slider("Urgent ratio", 0.0, 1.0, 0.3, 0.05)
    st.divider()
    start = st.button(
        "▶ Start Simulation", type="primary", use_container_width=True
    )

# ---------------------------------------------------------------------------
# Helper: order status icon
# ---------------------------------------------------------------------------


def _status_icon(status: str) -> str:
    mapping = {
        "pending": "⏳",
        "in_progress": "🔄",
        "completed": "✅",
        "rework": "🔧",
        "rejected": "❌",
        "failed": "⚠️",
    }
    return mapping.get(status, "❓")

# ---------------------------------------------------------------------------
# Main area — idle state
# ---------------------------------------------------------------------------

if not start:
    st.info("👈 Configure the simulation and click **Start** to begin.")
    st.stop()

# ---------------------------------------------------------------------------
# Setup simulation
# ---------------------------------------------------------------------------

settings = Settings()
orders = generate_orders(num_orders, urgent_ratio)
total = len(orders)

equipment = create_equipment()
chains = create_chains(settings)
bus = MessageBus()
agents = create_agents(equipment, chains, bus, settings)

thread_id = f"ui-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
config = create_config(
    thread_id=thread_id,
    equipment=equipment,
    agents=agents,
    chains=chains,
    bus=bus,
    scheduler_chain=agents["scheduler"].chain,
)

initial_state = SimulationState(
    pending_orders={o.id: o for o in orders},
    all_orders={o.id: o for o in orders},
)

# ---------------------------------------------------------------------------
# UI placeholders (updated live during stream)
# ---------------------------------------------------------------------------

progress_bar = st.progress(0, text="Initializing...")

col_left, col_right = st.columns([3, 2])

with col_left:
    st.subheader("📋 Live Events")
    event_placeholder = st.empty()

with col_right:
    st.subheader("📊 Stats")
    stats_placeholder = st.empty()

# ---------------------------------------------------------------------------
# Accumulators (updated each stream step)
# ---------------------------------------------------------------------------

events: list[str] = []
order_status: dict[str, str] = {o.id: "pending" for o in orders}
current_queue: list[str] = []
completed_count = 0
iteration = 0
re_plan_count = 0

# ---------------------------------------------------------------------------
# Run graph with streaming
# ---------------------------------------------------------------------------

try:
    with PostgresSaver.from_conn_string(settings.database_url) as checkpointer:
        checkpointer.setup()
        graph = build_graph(checkpointer)

        for update in graph.stream(
            initial_state, config, stream_mode="updates"
        ):
            node_name = list(update.keys())[0]
            data = update[node_name]

            # ---- plan node ----
            if node_name == "plan":
                queue = data.get("queue", [])
                reason = data.get("schedule_reason", "")
                re_plan_count = data.get("re_plan_count", re_plan_count)
                current_queue = list(queue)

                label = "Initial plan" if re_plan_count == 1 else "Re-plan"
                events.append(f"🔄 **{label} #{re_plan_count}:** {reason}")
                if queue:
                    preview = " → ".join(queue[:6])
                    if len(queue) > 6:
                        preview += f" … (+{len(queue) - 6})"
                    events.append(f"&nbsp;&nbsp;&nbsp;📋 Queue: {preview}")

            # ---- process_order node ----
            elif node_name == "process_order":
                result = data.get("pipeline_result", "")
                iteration = data.get("iteration", iteration)

                # Handle forced heat-press failure (special case)
                forced = data.get("heat_press_failure_triggered", False)
                if forced:
                    events.append("🔥🔥🔥 **FORCED HEAT PRESS FAILURE** 🔥🔥🔥")
                    events.append("🔧 Heat press repaired — re-planned")
                    current_queue = data.get("queue", current_queue)
                elif result:
                    # Determine which order was just processed
                    processed = current_queue[0] if current_queue else "?"

                    # Update queue for next iteration
                    current_queue = data.get("queue", current_queue)

                    if result == "completed":
                        completed_count = data.get(
                            "completed_count", completed_count
                        )
                        order_status[processed] = "completed"
                        events.append(
                            f"✅ **{processed}** completed "
                            f"({completed_count}/{total})"
                        )

                    elif result == "rework_qc":
                        order_status[processed] = "rework"
                        events.append(
                            f"🔧 **{processed}** needs rework — re-queued"
                        )

                    elif result == "rejected_qc":
                        order_status[processed] = "rejected"
                        events.append(
                            f"❌ **{processed}** failed QC — "
                            f"re-queued as urgent (full reprint)"
                        )

                    elif result.startswith("failed_"):
                        station = result.replace("failed_", "")
                        order_status[processed] = "failed"
                        events.append(
                            f"⚠️ **{processed}** failed at "
                            f"**{station.title()}** — re-queued"
                        )

                    elif result == "skip":
                        events.append(
                            f"⏭️ Skipped (already processed)"
                        )

                    elif result == "empty_queue":
                        events.append("📭 Queue empty")

                    else:
                        events.append(f"❓ Unknown result: {result}")

                # Track in-progress orders from state update
                in_progress = data.get("in_progress", {})
                for oid in in_progress:
                    if order_status.get(oid) != "completed":
                        order_status[oid] = "in_progress"

                # Track pending orders
                pending = data.get("pending_orders", {})
                for oid in pending:
                    if order_status.get(oid) not in ("completed",):
                        order_status[oid] = "pending"

            # ---- Update UI ----
            progress = completed_count / total if total else 0
            progress_bar.progress(
                progress,
                text=f"Progress: {completed_count}/{total} orders completed",
            )

            with event_placeholder.container():
                # Latest events at top
                for ev in reversed(events[-25:]):
                    st.markdown(ev)
                if not events:
                    st.caption("Waiting for first event...")

            with stats_placeholder.container():
                c1, c2, c3 = st.columns(3)
                c1.metric("Completed", f"{completed_count}/{total}")
                c2.metric("Iterations", iteration)
                c3.metric("Re-plans", re_plan_count)

                # Order status table
                st.markdown("##### Order Status")
                rows = []
                for o in orders:
                    sts = order_status.get(o.id, "pending")
                    rows.append(
                        {
                            "ID": o.id,
                            "Design": o.design_name,
                            "Prio": "⚡" if o.priority == "urgent" else "—",
                            "Status": f"{_status_icon(sts)} {sts}",
                        }
                    )
                st.dataframe(
                    rows,
                    width="stretch",
                    hide_index=True,
                    height=min(35 * len(rows) + 38, 400),
                )

except Exception as exc:
    st.error(f"Simulation failed: {exc}")
    raise

# ---------------------------------------------------------------------------
# Final summary (after stream ends)
# ---------------------------------------------------------------------------

st.divider()
st.header("📊 Simulation Complete")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Orders", total)
c2.metric("Completed", completed_count)
pending_final = total - completed_count
c3.metric("Pending / Failed", pending_final)
rate = f"{completed_count / total * 100:.1f}%" if total else "0%"
c4.metric("Completion Rate", rate)

st.caption(f"Re-plans: {re_plan_count}  •  Iterations: {iteration}")
st.caption(f"Thread: `{thread_id}`")

# Final order table
st.subheader("Final Order Details")
final_rows = []
for o in orders:
    sts = order_status.get(o.id, "unknown")
    final_rows.append(
        {
            "ID": o.id,
            "Design": o.design_name,
            "Priority": o.priority,
            "Status": f"{_status_icon(sts)} {sts}",
            "Reworks": o.rework_count,
        }
    )
st.dataframe(final_rows, width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# Entry point for `uv run ui`
# ---------------------------------------------------------------------------


def main() -> None:
    """Launch Streamlit server programmatically."""
    import streamlit.web.cli as stcli

    sys.argv = ["streamlit", "run", str(__file__)]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()

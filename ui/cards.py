"""Agent progress boxes built from NATIVE Streamlit components (no HTML, no CSS).
 
One bordered, full-width box per agent: icon, number + role, model tier, a coloured status with its timer, a progress bar, then two

columns - PROGRESS (what the agent did: tool calls, LLM calls, guardrails) and OUTPUT (what it produced). Streamlit is passed in as

`st`, so the functions can be tested with a recording stand-in and no browser. Text is markdown-escaped (it can carry customer or

document text, and Streamlit would otherwise format `*`, `_` or typeset `$...$`).

"""

import re
DEFAULT_ICONS = {"classifier": "🧭", "retriever": "🔎", "drafter": "✍️", "validator": "🛡️", "escalation": "🚨"}
COLOR = {"waiting": "gray", "running": "blue", "done": "green", "failed": "red", "skipped": "gray"}  # Streamlit colour names

LABEL = {"waiting": "Waiting", "running": "Running", "done": "Done", "failed": "Failed", "skipped": "Skipped"}

MARK = {"waiting": "⚪", "running": "🔄", "done": "✅", "failed": "❌", "skipped": "⏭️"}

BAR = {"waiting": 0.0, "skipped": 0.0, "running": 0.5, "done": 1.0, "failed": 1.0}

EMPTY = {"waiting": "Not started yet.", "running": "Working... the output appears when this agent finishes.",

         "done": "No output recorded.", "failed": "The agent failed; see the progress log.", "skipped": "Not needed for this request."}

ORCHESTRATOR_ICON = "🎛️"
 
 
def safe(value):

    """Escape markdown/LaTeX control characters so the text is shown literally."""

    return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>~$&])", r"\\\1", str(value)).replace("\n", "  \n")
 
 
def timer(card, elapsed):

    """'3.2 s' while running, '1.20 s' once finished, '' otherwise."""

    if card["status"] == "running" and elapsed is not None and card.get("started") is not None:

        return f"{max(elapsed - card['started'], 0):.1f} s"

    if card.get("duration") is not None and card["status"] in ("done", "failed"):

        return f"{card['duration']:.2f} s"

    return ""
 
 
def _progress(st, lines, status):

    st.caption("PROGRESS")

    if not lines:

        st.caption(EMPTY[status] if status in ("waiting", "skipped") else "No tool or model calls recorded.")

    for line in lines[-12:]:

        st.markdown(f"- {safe(line)}")
 
 
def _output(st, rows, status):

    st.caption("OUTPUT")

    if not rows:

        st.caption(EMPTY[status])

    for row in rows:

        st.markdown(f"**{safe(row['label'])}**  \n{safe(row['value'])}")
 
 
def _box(st, slot, icon, title, subtitle, status, clock, lines, rows, summary=""):

    with slot.container(border=True):

        icon_col, title_col, status_col = st.columns([1, 7, 3])

        icon_col.markdown(f"## {icon}")

        title_col.markdown(f"**{safe(title)}**")

        title_col.caption(safe(subtitle))

        status_col.markdown(f":{COLOR[status]}[**{MARK[status]} {LABEL[status]}**]")

        if clock:

            status_col.caption(clock)

        st.progress(BAR[status])

        left, right = st.columns(2)

        with left:

            _progress(st, lines, status)

        with right:

            _output(st, rows, status)

        if summary:

            st.caption(safe(summary))
 
 
def render_agent(st, slot, spec, meta, card, elapsed=None, number=None):

    """Draw one agent box into `slot` (st, st.empty() or st.container()). `card` is a view-model card or an exported turn's agent record."""

    title = f"{number} · {spec['label']}" if number else spec["label"]

    _box(st, slot, spec.get("icon") or DEFAULT_ICONS.get(spec.get("key"), "🤖"), title, f"{meta['role']} · {meta['tier']} model", card["status"], timer(card, elapsed),

         card.get("activity") or [], card.get("output") or [], card.get("summary") or "")
 
 
def render_orchestrator(st, slot, calls, outcome, finished):

    """The orchestrator's own deterministic steps (KYC, ownership, profile, delivery, audit) and the final outcome."""

    status = "done" if finished else "running" if calls else "waiting"

    _box(st, slot, ORCHESTRATOR_ICON, "Orchestrator", "Deterministic checks, delivery and audit over MCP", status,

         f"{len(calls)} MCP call{'s' if len(calls) != 1 else ''}", list(calls), outcome if finished else [])
 
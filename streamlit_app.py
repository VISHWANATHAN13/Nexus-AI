


"""Nexus concierge - Streamlit UI.
 
    streamlit run streamlit_app.py
 
Streams the five agents' progress live while a request runs, keeps the chat history, and offers Markdown / JSON downloads

of every query + response. The backend is used as-is: requests go through nexus_orchestrator.pipeline.handle_request (free

text) or scenario_runner.run_scenario (demo_scenarios.yaml); progress is observed by ui/progress.py.

Modes: Live = real CrewAI agents + OpenAI + MCP server processes (needs OPENAI_API_KEY in .env).

       Demo = the scripted stand-in agents used by the offline tests, with in-process tool calls (no OpenAI).

"""

import os

import time
 
import streamlit as st
 
st.set_page_config(page_title="Nexus Concierge", page_icon="🏦", layout="wide")
 
 
@st.cache_resource

def boot():

    """Once per server process: .env, telemetry off, scenarios."""

    from config import setup_environment

    setup_environment()

    import scenario_runner

    return scenario_runner.load_scenarios()
 
 
SCENARIOS = boot()
 
import scenario_runner  # noqa: E402

from ui import cards, export, progress  # noqa: E402
 
STATUS_COLOR = {"resolved": "green", "escalated": "orange", "rejected": "red", "error": "red"}

META = progress.agent_meta()
 
st.session_state.setdefault("turns", [])

st.session_state.setdefault("job", None)

st.session_state.setdefault("raw_queries", [])
 
 
def stream(job):

    """Redraw the cards from the job's events until the worker thread finishes, then store the turn."""

    with st.chat_message("assistant", avatar="🏦"):

        bar = st.progress(0.0, text="Starting…")

        slots = [st.empty() for _ in progress.AGENTS]  # one full-width box per agent, stacked in pipeline order

        orchestrator = st.empty()

        while True:

            state = progress.state_of(job)

            elapsed = time.perf_counter() - job["t0"]

            finished = sum(card["status"] in progress.FINISHED for card in state["agents"].values())

            running = [a["label"] for a in progress.AGENTS if state["agents"][a["key"]]["status"] == "running"]

            text = ("Waiting for the previous request to finish…" if state["queued"] else

                    f"{', '.join(running) or 'Orchestrator'} working… {elapsed:.0f} s")

            bar.progress(min(finished / len(progress.AGENTS), 1.0), text=text)

            for number, (slot, spec) in enumerate(zip(slots, progress.AGENTS), 1):

                cards.render_agent(st, slot, spec, META[spec["key"]], state["agents"][spec["key"]], elapsed, number)

            cards.render_orchestrator(st, orchestrator, state["orchestrator"], state.get("outcome", []), job["done"])

            if job["done"]:

                break

            time.sleep(0.3)

    problems = scenario_runner.judge(job["scenario"], job["result"]) if job["scenario"] and job["result"] else None

    st.session_state.turns.append(export.build_turn(job, progress.state_of(job), problems))

    st.session_state.job = None

    st.rerun()
 
 
def show_turn(index, turn, raw_query):

    with st.chat_message("user", avatar="🧑"):

        st.markdown(raw_query)

        if turn["scenario"]:

            st.caption(f"scenario: {turn['scenario']}")

    with st.chat_message("assistant", avatar="🏦"):

        color = STATUS_COLOR.get(turn["status"], "gray")

        st.markdown(f":{color}[**{export.STATUS_LABEL.get(turn['status'], turn['status'])}**]"

                    + (f" &nbsp; `{turn['error']['code']}`" if turn["error"] else "")

                    + (f" &nbsp; · {turn['duration_s']:.1f} s" if turn["duration_s"] is not None else "")

                    + f" &nbsp; · {turn['mode']} mode")

        st.markdown(turn["response"] or "_(no customer message)_")

        if turn["scenario_check"] is not None:

            if turn["scenario_check"]:

                st.error("Scenario check FAILED: " + "; ".join(turn["scenario_check"]))

            else:

                st.success("Scenario check PASSED (status, servers, rule, error code, masking)")

        with st.expander("Agent progress and output", expanded=index == len(st.session_state.turns) - 1):  # the latest turn is open

            for number, (spec, card) in enumerate(zip(progress.AGENTS, turn["agents"]), 1):

                cards.render_agent(st, st, spec, META[spec["key"]], {**card, "started": None}, None, number)

            cards.render_orchestrator(st, st, turn["orchestrator_calls"], turn.get("outcome", []), True)

        with st.expander("Evidence: routing, grounding, decision", expanded=False):

            left, right = st.columns(2)

            with left:

                st.markdown("**MCP tool calls** (from the servers' own trace)")

                st.dataframe(turn["mcp_calls"] or [{"server": "-", "tool": "no MCP tool call was made"}], hide_index=True,

                             use_container_width=True)

                if turn["classification"]:

                    st.markdown("**Classification**")

                    st.json(turn["classification"])

            with right:

                st.markdown("**Grounded facts**")

                for fact in turn["facts"]:

                    st.markdown(f"- `{fact['source']}` {fact['text']}")

                if not turn["facts"]:

                    st.caption("none")

                if turn["conflicts"]:

                    st.warning(f"{turn['conflicts']} retrieval conflict(s): live MCP data wins")

                if turn["quarantined"]:

                    st.warning(f"Quarantined (prompt injection): {', '.join(turn['quarantined'])}")

                if turn["validation"]:

                    st.markdown("**Validation**")

                    st.json(turn["validation"])

                if turn["escalation"]:

                    st.markdown("**Escalation decision** (deterministic run_escalation_check)")

                    st.json(turn["escalation"])

        name = f"nexus_{(turn['request_id'] or 'request').lower()}_{index + 1}"

        a, b, _ = st.columns([1, 1, 4])

        a.download_button("⬇ Markdown", export.turn_markdown(turn, index + 1), file_name=f"{name}.md", mime="text/markdown",

                          key=f"md_{index}")

        b.download_button("⬇ JSON", export.turn_json(turn), file_name=f"{name}.json", mime="application/json", key=f"json_{index}")
 
 
# ------------------------------------------------------------------------------------------------ sidebar

has_key = bool(os.environ.get("OPENAI_API_KEY"))

busy = st.session_state.job is not None

with st.sidebar:

    st.header("Nexus concierge")

    st.caption("Meridian Financial Group - 5 CrewAI agents over 4 MCP servers")

    mode_label = st.radio("Mode", ["Live (OpenAI + MCP servers)", "Demo (scripted agents, no OpenAI)"], index=0 if has_key else 1,

                          disabled=busy, help="Demo mode uses the same scripted agents as the offline tests; tool calls run in-process.")

    mode = "live" if mode_label.startswith("Live") else "demo"

    if mode == "live" and not has_key:

        st.error("OPENAI_API_KEY is not set (.env). Live mode will fail; use Demo mode or add the key and restart.")

    st.divider()

    st.subheader("Demo scenarios")

    titles = {f"{s['name']} - {s['title']}": s for s in SCENARIOS}

    picked = titles[st.selectbox("Scenario", list(titles), disabled=busy)]

    st.caption(picked["text"])

    if picked.get("offline"):

        st.caption(f"switch: {', '.join(picked['offline'])} offline")

    if mode == "demo" and picked.get("needs_demo_index"):

        st.warning("This scenario needs the real knowledge search (Live mode + `python build_index.py --demo`).")

    run_scenario = st.button("▶ Run scenario", disabled=busy, use_container_width=True)

    st.divider()

    st.subheader("Download chat")

    turns = st.session_state.turns

    st.download_button("⬇ Whole chat (Markdown)", export.chat_markdown(turns), file_name="nexus_chat.md", mime="text/markdown",

                       disabled=not turns, use_container_width=True)

    st.download_button("⬇ Whole chat (JSON)", export.chat_json(turns), file_name="nexus_chat.json", mime="application/json",

                       disabled=not turns, use_container_width=True)

    if st.button("🗑 Clear chat", disabled=busy or not turns, use_container_width=True):

        st.session_state.turns, st.session_state.raw_queries = [], []

        st.rerun()

    st.caption("One request runs at a time. Account, policy and portfolio numbers are masked in downloads.")
 
# ------------------------------------------------------------------------------------------------ main

st.title("🏦 Nexus Concierge")

st.caption("Classifier → Retriever → Drafter → Validator → Escalation, live. Start your message with your customer id, "

           "e.g. *I am CUS-20077 and my account is ACC-20077. What is my balance?*")
 
prompt = st.chat_input("Type your request…", disabled=busy)

if not busy and (prompt or run_scenario):

    scenario = picked if run_scenario and not prompt else None

    text = scenario["text"] if scenario else prompt

    st.session_state.raw_queries.append(text)

    st.session_state.job = progress.start_job(progress.new_job(text, scenario, mode))
 
for i, (turn, raw) in enumerate(zip(st.session_state.turns, st.session_state.raw_queries)):

    show_turn(i, turn, raw)
 
job = st.session_state.job

if job is not None:

    with st.chat_message("user", avatar="🧑"):

        st.markdown(job["text"])

        if job["scenario"]:

            st.caption(f"scenario: {job['scenario']['name']}")

    stream(job)

elif not st.session_state.turns:

    st.info("Type a request below or run a demo scenario from the sidebar.")
 
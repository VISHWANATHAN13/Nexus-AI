"""The agent boxes (native Streamlit): one bordered box per agent with icon, progress log and output; markdown-escaped; identifiers

masked. Streamlit itself is replaced by a recording stand-in, so these tests need no browser and no streamlit install."""

import json
 
import conftest  # noqa: F401

from helpers import need
 
SHOWCASE = ("I am CUS-20077. My savings account is ACC-20077 and my policy is POL-IN-30091. "

            "Can I set up auto-debit for my insurance premium from my savings account?")
 
 
class Box:

    """A container / column / chat element: records what is drawn into it and works as a context manager."""

    def __init__(self, st):

        self.st = st
 
    def __enter__(self):

        return self
 
    def __exit__(self, *exc):

        return False
 
    def container(self, border=False):

        return Box(self.st)
 
    def markdown(self, text, **kw):

        self.st.calls.append(("markdown", text))
 
    def caption(self, text, **kw):

        self.st.calls.append(("caption", text))
 
    def progress(self, value, **kw):

        self.st.calls.append(("progress", value))
 
    def columns(self, spec):

        return [Box(self.st) for _ in range(len(spec) if isinstance(spec, (list, tuple)) else spec)]
 
 
class FakeSt(Box):

    def __init__(self):

        self.st, self.calls = self, []
 
    def text(self):

        return "\n".join(str(value) for _, value in self.calls)
 
 
def agent(key):

    from ui import progress

    return next(a for a in progress.AGENTS if a["key"] == key), progress.agent_meta()[key]
 
 
def test_agent_box_uses_native_streamlit_with_icon_progress_and_output():

    from ui import cards, progress

    st, (spec, meta) = FakeSt(), agent("drafter")

    card = progress.new_state()["agents"]["drafter"]

    card.update(status="done", duration=1.234, summary="done *ok*", activity=["🔧 tool(<script>alert(1)</script>)"],

                output=[{"label": "Draft", "value": "Cost $5 and *bold* <i>x</i>"}])

    cards.render_agent(st, st, spec, meta, card, None, 3)

    text = st.text()

    assert "✍️" in text and "3 · Drafter" in text and ":green[" in text and "Done" in text and "1.23 s" in text

    assert ("progress", 1.0) in st.calls and "PROGRESS" in text and "OUTPUT" in text and "Draft" in text

    assert "\\$5" in text and "\\*bold\\*" in text and "\\<script\\>" in text  # shown literally, never formatted or typeset

    assert "<div" not in text and "<style" not in text  # no HTML anywhere
 
    st = FakeSt()

    cards.render_agent(st, st, spec, meta, progress.new_state()["agents"]["drafter"], 0.0, 3)

    assert ("progress", 0.0) in st.calls and "Not started yet" in st.text() and ":gray[" in st.text()

    running = progress.new_state()["agents"]["validator"]

    running.update(status="running", started=1.0)

    st = FakeSt()

    cards.render_agent(st, st, *agent("validator"), running, 3.5, 4)

    assert ("progress", 0.5) in st.calls and "2.5 s" in st.text() and ":blue[" in st.text()

    assert all(a["icon"] for a in progress.AGENTS) and len({a["icon"] for a in progress.AGENTS}) == 5
 
 
def test_every_agent_box_shows_its_output_after_a_run_and_nothing_unmasked():

    need("pydantic")

    from ui import cards, progress

    job = progress.run_job(progress.new_job(SHOWCASE, None, "demo"))

    state = progress.state_of(job)

    for key in ("classifier", "retriever", "drafter", "validator", "escalation"):

        assert state["agents"][key]["output"], key

    labels = {key: [row["label"] for row in state["agents"][key]["output"]] for key in state["agents"]}

    assert "Product line" in labels["classifier"] and "Draft" in labels["drafter"] and "Confidence" in labels["validator"]

    assert any(label.startswith("fact · banking") for label in labels["retriever"]) and "Decision" in labels["escalation"]

    assert {r["label"]: r["value"] for r in state["agents"]["classifier"]["output"]}["Product line"] == "cross_product"

    st = FakeSt()

    for number, spec in enumerate(progress.AGENTS, 1):

        cards.render_agent(st, st, spec, progress.agent_meta()[spec["key"]], state["agents"][spec["key"]], None, number)

    cards.render_orchestrator(st, st, state["orchestrator"], state["outcome"], True)

    page = st.text()

    assert all(spec["icon"] in page for spec in progress.AGENTS) and cards.ORCHESTRATOR_ICON in page

    assert "ACC-20077" not in page and "POL-IN-30091" not in page and "get\\_customer\\_kyc\\_status" in page and "RESOLVED" in page

    assert json.dumps(state["agents"])  # the view model stays JSON-serialisable (exported with the chat)
 
 
def test_early_exit_escalation_is_visible_on_the_escalation_box():

    need("pydantic")

    from ui import progress

    job = progress.run_job(progress.new_job("I am CUS-00005. I lost my job and can't pay my insurance premium any more.", None, "demo"))

    state = progress.state_of(job)

    rows = {r["label"]: r["value"] for r in state["agents"]["escalation"]["output"]}

    assert rows["Decision"].startswith("ESCALATE") and rows["Route"] == "financial_support_team" and rows["Early exit"] == "kyc_not_verified"

    assert state["agents"]["retriever"]["status"] == "skipped" and {r["label"]: r["value"] for r in state["outcome"]}["Status"] == "ESCALATED"
 
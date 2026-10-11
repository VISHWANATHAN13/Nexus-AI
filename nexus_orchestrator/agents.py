"""Five CrewAI agents with a shared LLM wrapper (tier-based), task runner, JSON extraction, guardrails.

No classes: just functions. Task guardrails make agents retry on bad output; escalation_guardrail calls the tool.
"""
import json
import logging

from config import env_switch, load_config, load_yaml
from guardrails import check_output, redact_for_logging
from logging_config import get_logger, log_json, trace
from nexus_orchestrator.intake import ClassifierOutput

logger = get_logger("agents")


def _r(data):
    return redact_for_logging(data)


_LLM_CACHE = {}


@trace(logger, redact=_r)
def make_llm(tier):
    """CrewAI LLM for a tier ('small' | 'mid' | 'top') using the model names in config.yaml. One object per distinct
    setting is reused for the life of the process (building it for every task cost ~1 s each)."""
    from crewai import LLM
    llm_cfg = load_config()["llm"]
    settings = {"model": llm_cfg["provider_prefix"] + llm_cfg[tier]}
    if llm_cfg.get("temperature") is not None:
        settings["temperature"] = llm_cfg["temperature"]
    key = tuple(sorted(settings.items()))
    if key not in _LLM_CACHE:
        _LLM_CACHE[key] = LLM(**settings)
    return _LLM_CACHE[key]


@trace(logger, redact=_r)
def render(template, values):
    """Fill <<name>> placeholders (plain replacement, so JSON braces in values are safe)."""
    for name, value in values.items():
        template = template.replace(f"<<{name}>>", str(value))
    return template


@trace(logger, redact=_r)
def extract_json(raw):
    """First JSON object found in a model's text output, or None."""
    text = str(raw)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


@trace(logger, redact=_r)
def decision_from(raw):
    """The escalation decision dict from the agent's answer; accepts the tool's envelope {"ok":..., "data": {...}} or the bare decision."""
    data = extract_json(raw)
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    return data


@trace(logger, redact=_r)
def run_task(agent_name, task_name, values, tools=None, output_pydantic=None, guardrail=None):
    """Build the agent + task from YAML, run them in a one-task Crew, return the CrewAI result."""
    from crewai import Agent, Crew, Process, Task
    agent_cfg = load_yaml("agents.yaml")[agent_name]
    task_cfg = load_yaml("tasks.yaml")[task_name]
    agent = Agent(role=agent_cfg["role"], goal=agent_cfg["goal"], backstory=agent_cfg["backstory"],
                  llm=make_llm(agent_cfg["tier"]), tools=tools or [], allow_delegation=False, verbose=False)
    task_settings = {"description": render(task_cfg["description"], values),
                     "expected_output": render(task_cfg["expected_output"], values), "agent": agent}
    if output_pydantic is not None:
        task_settings["output_pydantic"] = output_pydantic
    if guardrail is not None:
        task_settings["guardrail"] = guardrail
        task_settings["guardrail_max_retries"] = load_config()["llm"]["guardrail_retries"]
    return Crew(agents=[agent], tasks=[Task(**task_settings)], process=Process.sequential, verbose=False).kickoff()


# ----------------------------------------------------------------------------- guardrail functions
@trace(logger, redact=_r)
def classifier_guardrail(output):
    """CrewAI task guardrail: the output must parse into ClassifierOutput."""
    data = extract_json(output.raw)
    try:
        ClassifierOutput(**(data or {}))
    except Exception as exc:  # pydantic ValidationError -> tell the agent what is wrong
        return False, f"Your JSON is invalid: {str(exc)[:300]}"
    return True, output


@trace(logger, redact=_r)
def retriever_guardrail(output):
    """Facts need a source (a known MCP server or kb:<doc>), conflicts/gaps must be lists."""
    data = extract_json(output.raw)
    if not isinstance(data, dict) or not all(key in data for key in ("facts", "conflicts", "gaps")):
        return False, "Return a JSON object with the keys facts, conflicts and gaps."
    servers = load_config()["mcp"]["servers"]
    for fact in data["facts"] if isinstance(data["facts"], list) else [None]:
        if not isinstance(fact, dict) or not fact.get("text") or not (
                str(fact.get("source", "")).startswith("kb:") or fact.get("source") in servers):
            return False, f"Every fact needs a text and a source that is one of {servers} or 'kb:<document id>'."
    if not isinstance(data["conflicts"], list) or not isinstance(data["gaps"], list):
        return False, "conflicts and gaps must be lists."
    return True, output


@trace(logger, redact=_r)
def make_draft_guardrail(facts_text):
    """Output filtering (spec 4): no unmasked identifiers, no figures that are not in the facts."""
    def draft_guardrail(output):
        verdict = check_output(str(output.raw), facts_text)
        if not verdict["ok"]:
            return False, "Rewrite the reply. Problems: " + "; ".join(i["detail"] for i in verdict["issues"])
        return True, output
    return draft_guardrail


@trace(logger, redact=_r)
def validator_guardrail(output):
    data = extract_json(output.raw)
    confidence = data.get("confidence") if isinstance(data, dict) else None
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return False, "Return JSON with confidence as a number between 0 and 1, plus answers_question (true/false), issues and conflicts_seen lists."
    if not isinstance(data.get("answers_question"), bool):
        return False, "answers_question must be true or false."
    if not isinstance(data.get("issues"), list) or not isinstance(data.get("conflicts_seen"), list):
        return False, "issues and conflicts_seen must be lists."
    return True, output


@trace(logger, redact=_r)
def escalation_guardrail(output):
    data = decision_from(output.raw)
    if not isinstance(data, dict) or "escalate" not in data or "rule" not in data:
        return False, "Call the run_escalation_check tool and return its JSON result (escalate, rule, category, route_to)."
    return True, output


@trace(logger, redact=_r)
def screen_guardrail(output):
    data = extract_json(output.raw)
    if not isinstance(data, dict) or data.get("escalation_category") not in load_config()["escalation"]["categories"]:
        return False, f"Return JSON with escalation_category as one of {load_config()['escalation']['categories']} and a short reason."
    return True, output


@trace(logger, redact=_r)
def stricter_category(first, second):
    """The more serious of two escalation categories (order: signals/escalation.yaml priority); 'none' if neither is a hard one."""
    priority = load_yaml("signals/escalation.yaml")["priority"]
    candidates = [c for c in (first, second) if c in priority]
    return min(candidates, key=priority.index) if candidates else "none"


# ----------------------------------------------------------------------------- the five stages
@trace(logger, redact=_r)
def classify(prompt, customer_text):
    """Agent 1 - Classifier (small model). Returns the ClassifierOutput fields as a dict, or None."""
    result = run_task("classifier", "classify", {"prompt": prompt, "customer_text": customer_text},
                      output_pydantic=ClassifierOutput, guardrail=classifier_guardrail)
    fields = result.pydantic.model_dump() if getattr(result, "pydantic", None) else extract_json(result.raw)
    if isinstance(fields, dict) and fields.get("escalation_category", "none") == "none":  # a hard category needs no second opinion
        fields["escalation_category"] = stricter_category("none", screen(customer_text))
    return fields


@trace(logger, redact=_r)
def screen(customer_text):
    """Independent second opinion on escalation_category (a separate prompt that is told to flag when unsure). It can only RAISE
    the category, never lower it; if it is disabled, fails or returns nonsense the request carries on exactly as before.
    Config: escalation.llm_screen.enabled."""
    if not (load_config()["escalation"].get("llm_screen") or {}).get("enabled"):
        return None
    try:
        data = extract_json(run_task("screener", "screen", {"customer_text": customer_text}, guardrail=screen_guardrail).raw)
    except Exception as exc:  # never let the extra safety net take the request down
        log_json(logger, logging.WARNING, "screen_failed", error_type=type(exc).__name__)
        return None
    category = data.get("escalation_category") if isinstance(data, dict) else None
    return category if category in load_config()["escalation"]["categories"] else None


@trace(logger, redact=_r)
def retrieve(values, tools):
    """Agent 2 - Retriever (mid model) with the LangChain search tool and the live MCP tools. Returns facts/conflicts/gaps."""
    return extract_json(run_task("retriever", "retrieve", values, tools=tools, guardrail=retriever_guardrail).raw)


@trace(logger, redact=_r)
def draft(values, facts_text):
    """Agent 3 - Drafter (mid model). Returns the reply text."""
    return str(run_task("drafter", "draft", values, guardrail=make_draft_guardrail(facts_text)).raw).strip()


@trace(logger, redact=_r)
def handoff(values):
    """Agent 3 (handoff variant) - the short note shown to a customer whose request goes to a human."""
    return str(run_task("drafter", "handoff", values).raw).strip()


@trace(logger, redact=_r)
def validate(values):
    """Agent 4 - Validator (top model). Returns {"confidence", "answers_question", "issues", "conflicts_seen"}.
    A draft that does not answer the question has its confidence capped (config escalation.unanswered_confidence_cap), however
    sure the model is. In test mode NEXUS_FORCE_CONFIDENCE overrides the score (stress test 3: escalation must hold even at high
    confidence)."""
    report = extract_json(run_task("validator", "validate", values, guardrail=validator_guardrail).raw)
    if report["answers_question"] is False:
        report["confidence"] = min(report["confidence"], load_config()["escalation"]["unanswered_confidence_cap"])
    forced = env_switch("NEXUS_FORCE_CONFIDENCE")  # honoured only in test mode (NEXUS_TEST_MODE=1), never in production
    if forced:
        report["confidence"] = float(forced)
    return report


@trace(logger, redact=_r)
def escalate(values, tools):
    """Agent 5 - Escalation (mid model): calls run_escalation_check through MCP. Returns the tool's JSON as the agent reported it."""
    return decision_from(run_task("escalation", "escalate", values, tools=tools, guardrail=escalation_guardrail).raw)
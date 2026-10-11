"""Pydantic intake validation (spec 3.2). ClassifierOutput is what the Classifier agent must produce;
Intake adds request_id, customer_id, references and validates everything before any retrieval, drafting or tool call happens.
A failed validation is a typed error, never a silent pass.
"""
import re
import uuid

from pydantic import BaseModel, Field, ValidationError, field_validator

from config import load_config, load_yaml
from errors import error_response
from guardrails import find_ids_in_text, redact_for_logging, sanitize_free_text, validate_id
from logging_config import get_logger, trace

logger = get_logger("intake")


def _r(data):
    return redact_for_logging(data)


@trace(logger, redact=_r)
def allowed_values(field):
    """Allowed enum values for a field, read from config.yaml."""
    cfg = load_config()
    return {"product_line": cfg["intake"]["product_lines"], "request_type": cfg["intake"]["request_types"],
            "urgency": cfg["intake"]["urgencies"], "escalation_category": cfg["escalation"]["categories"]}[field]


class ClassifierOutput(BaseModel):
    """What the Classifier agent must produce (the model never supplies ids)."""
    product_line: str
    request_type: str
    urgency: str
    customer_stated_intent: str
    escalation_category: str

    @field_validator("product_line", "request_type", "urgency", "escalation_category")
    @classmethod
    def must_be_allowed(cls, value, info):
        if value not in allowed_values(info.field_name):
            raise ValueError(f"must be one of {allowed_values(info.field_name)}")
        return value

    @field_validator("customer_stated_intent")
    @classmethod
    def must_be_clean_text(cls, value):
        if not value.strip():
            raise ValueError("must not be empty")
        return sanitize_free_text(value, max_len=load_config()["text"]["customer_max_len"])  # sanitised before storage/logging


class Intake(ClassifierOutput):
    """The validated intake record (spec 3.2 schema)."""
    request_id: str = Field(default_factory=lambda: "REQ-" + uuid.uuid4().hex[:10].upper())
    customer_id: str
    references: dict[str, str] = Field(default_factory=dict)

    @field_validator("customer_id")
    @classmethod
    def customer_id_format(cls, value):
        return validate_id("customer", value)

    @field_validator("references")
    @classmethod
    def reference_formats(cls, value):
        for kind, identifier in value.items():
            validate_id(kind, identifier)
        return value


@trace(logger, redact=_r)
def precheck_text(text):
    """Deterministic checks on the sanitised text BEFORE any model call. Returns {"ok", "customer_id", "references"} or an error."""
    ids = find_ids_in_text(text)
    customers = ids.get("customer", [])
    if len(customers) > 1:
        return error_response("AMBIGUOUS_CUSTOMER_ID", "More than one customer ID was given")
    if not customers:
        if re.search(r"\bCUS-\S*", text):
            return error_response("INVALID_CUSTOMER_ID", "The customer ID does not match the CUS-XXXXX format")
        return error_response("MISSING_CUSTOMER_ID", "No customer ID was given")
    references = {kind: found[0] for kind, found in ids.items() if kind != "customer"}
    return {"ok": True, "customer_id": customers[0], "references": references}


@trace(logger, redact=_r)
def merge_escalation(model_category, text):
    """The stricter of the model's category and the phrase-signal scan wins (so rephrasing cannot hide a hard category)."""
    from guardrails import detect_escalation_category
    signal = detect_escalation_category(text)
    priority = load_yaml("signals/escalation.yaml")["priority"]
    candidates = [c for c in (model_category, signal) if c in priority]
    return min(candidates, key=priority.index) if candidates else "none"


@trace(logger, redact=_r)
def build_intake(customer_id, references, fields):
    """Validate everything into an Intake. Returns {"ok": True, "intake": Intake} or a typed INTAKE_REJECTED error."""
    try:
        return {"ok": True, "intake": Intake(customer_id=customer_id, references=references, **fields)}
    except ValidationError as exc:
        problems = [{"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]} for e in exc.errors()]
        return error_response("INTAKE_REJECTED", "Intake validation failed; the request was not processed", {"errors": problems})


@trace(logger, redact=_r)
def merge_product_line(model_line, references):
    """Identifiers from more than one domain (e.g. an account AND a policy) make the request cross_product, whatever the model said."""
    owners = load_config()["routing"]["reference_server"]
    lines = {owners[kind].replace("_server", "") for kind in references if kind in owners}
    return "cross_product" if len(lines) > 1 else model_line


@trace(logger, redact=_r)
def servers_for(product_line, references):
    """MCP servers the Retriever may use: those of the product line plus the owners of every identifier the customer gave."""
    cfg = load_config()["routing"]
    wanted = list(cfg["product_line_servers"][product_line]) + [cfg["reference_server"][k] for k in references if k in cfg["reference_server"]]
    return list(dict.fromkeys(wanted))
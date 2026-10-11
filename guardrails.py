

"""Guardrails (spec section 4): ID validation, masking, scoped fields, sanitisation, prompt-injection defence,

output filtering and PII-safe logging. Patterns, scopes and limits come from config.yaml / signals/*.yaml.
 
Tracing note: the redaction primitives (redact_for_logging, scrub_text, _fingerprint, _mask_value) and the `_r` / `_rt`

adapters are called BY the tracer, so they are not traced themselves (that would recurse). Every other function is traced.

"""

import base64

import hashlib

import html

import re

import unicodedata
 
from config import load_config, load_yaml

from errors import fail

from logging_config import get_logger, trace
 
logger = get_logger("guardrails")

REMOVED = "[REMOVED: suspected prompt injection]"
 
 
def _r(data):  # late-bound so traced guardrail functions never log raw PII

    return redact_for_logging(data)
 
 
def _rt(data):  # for functions whose RETURN value is customer free text: never log it verbatim

    if isinstance(data, str):

        return _fingerprint(data)

    if isinstance(data, (list, tuple)):

        return [_rt(item) for item in data]

    return redact_for_logging(data)
 
 
# --------------------------------------------------------------------------- identifiers

@trace(logger, redact=_r)

def validate_id(kind, value):

    """Typed error INVALID_ID_FORMAT unless value matches the configured format for this kind of identifier."""

    patterns = load_config()["ids"]

    if kind not in patterns:

        fail("INVALID_ARGUMENT", f"Unknown identifier kind '{kind}'")

    if not isinstance(value, str) or not re.fullmatch(patterns[kind], value):  # fullmatch: '$' alone would accept 'CUS-20077\n'

        fail("INVALID_ID_FORMAT", f"Invalid {kind} identifier format", {"kind": kind})

    return value
 
 
@trace(logger, redact=_r)

def find_ids_in_text(text):

    """All identifiers of every kind found in free text, e.g. {'customer': ['CUS-20077'], 'account': [...]}."""

    found = {}

    for kind, pattern in load_config()["ids"].items():

        loose = pattern.strip("^$")

        found[kind] = sorted(set(re.findall(r"\b" + loose + r"\b", text)))

    return {kind: ids for kind, ids in found.items() if ids}
 
 
@trace(logger, redact=_r)

def mask_identifier(value):

    """Keep only the last four characters: ACC-20077 -> ****0077."""

    return "****" + str(value)[-4:]
 
 
@trace(logger, redact=_r)

def mask_identifiers_in_text(text):

    patterns = load_config()["ids"]

    for kind in load_config()["masked_kinds"]:

        loose = patterns[kind].strip("^$")

        text = re.sub(r"\b" + loose + r"\b", lambda m: "****" + m.group(0)[-4:], text)

    return text
 
 
# --------------------------------------------------------------------------- scoped field visibility

@trace(logger, redact=_r)

def check_scope(caller_scope):

    if caller_scope not in load_config()["scopes"]["order"]:

        fail("INVALID_SCOPE", "Unknown caller_scope", {"allowed": load_config()["scopes"]["order"]})

    return caller_scope
 
 
@trace(logger, redact=_r)

def require_scope(caller_scope, minimum):

    order = load_config()["scopes"]["order"]

    if order.index(check_scope(caller_scope)) < order.index(minimum):

        fail("ACCESS_DENIED", f"caller_scope '{caller_scope}' is below the '{minimum}' required here")
 
 
@trace(logger, redact=_r)

def _filter(record, kind, caller_scope, id_field, masked_name):

    out = {}

    for name in load_config()["scopes"][kind][check_scope(caller_scope)]:

        if name == masked_name:

            out[name] = mask_identifier(record[id_field])

        elif name == "auto_debit_enabled":

            out[name] = bool(record.get(name))

        else:

            out[name] = record.get(name)

    return out
 
 
@trace(logger, redact=_r)

def minimize_account_fields(record, caller_scope):

    return _filter(record, "account", caller_scope, "account_id", "account_number")
 
 
@trace(logger, redact=_r)

def minimize_portfolio_fields(record, caller_scope):

    return _filter(record, "portfolio", caller_scope, "portfolio_id", "portfolio_number")
 
 
@trace(logger, redact=_r)

def minimize_product_fields(record, caller_scope):

    return _filter(record, "product", caller_scope, "product_id", None)  # catalogue ids stay readable (and unique): spec 4 masks account/policy/portfolio numbers only
 
 
# --------------------------------------------------------------------------- sanitisation / injection defence

_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff]")

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_COMMENTS = re.compile(r"<!--.*?-->", re.S)

_SCRIPTS = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.S | re.I)

_TAGS = re.compile(r"<[^>]*>")
 
 
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})

_SPACED_LETTERS = re.compile(r"\b(?:[A-Za-z] ){2,}[A-Za-z]\b")  # "i g n o r e"

_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")
 
 
@trace(logger, redact=_rt)

def _deleet(text):

    """Map look-alike digits/symbols back to letters, but only inside tokens that also contain letters (so numbers stay numbers)."""

    def token(match):

        word = match.group(0)

        return word.translate(_LEET) if re.search(r"[A-Za-z]", word) and re.search(r"[0-9@$]", word) else word

    return re.sub(r"\S+", token, text)
 
 
@trace(logger, redact=_rt)

def _decoded_base64(text):

    """Printable UTF-8 text hidden in long base64 runs (a common way to smuggle an instruction past keyword filters)."""

    found = []

    for run in _BASE64_RUN.findall(text)[:5]:

        try:

            decoded = base64.b64decode(run + "=" * (-len(run) % 4)).decode("utf-8")

        except (ValueError, UnicodeDecodeError):

            continue

        if len(decoded) >= 8 and sum(ch.isprintable() or ch.isspace() for ch in decoded) / len(decoded) > 0.95:

            found.append(decoded)

    return found
 
 
@trace(logger, redact=_rt)

def _variants(text):

    """The text plus de-obfuscated readings of it. Detection only: the text that is stored or returned is never altered by this."""

    despaced = _SPACED_LETTERS.sub(lambda m: m.group(0).replace(" ", ""), text)

    variants = {text, despaced, _deleet(text), _deleet(despaced)}

    for decoded in _decoded_base64(text):

        variants.add(decoded)

    return variants
 
 
@trace(logger, redact=_r)

def _signatures(text):

    signatures = load_yaml("signals/injection.yaml")

    hits = set()

    for variant in _variants(text):

        hits |= {name for name, pattern in signatures.items() if re.search(pattern, variant, re.I)}

    return hits
 
 
@trace(logger, redact=_rt)

def sanitize_free_text(text, max_len=1000, reject_too_long=False):

    """Length limit + HTML stripping + injection-signature detection. Typed error INJECTION_DETECTED.
 
    Detection runs on the raw, entity-decoded and tag-stripped text, so an instruction hidden in an HTML comment or

    encoded as entities is caught BEFORE stripping would hide it.

    """

    if not isinstance(text, str):

        fail("INVALID_ARGUMENT", "Free text must be a string")

    normalised = _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text))

    decoded = html.unescape(normalised)

    stripped = _TAGS.sub(" ", _SCRIPTS.sub(" ", _COMMENTS.sub(" ", decoded)))

    hits = set()

    for form in {normalised, decoded, stripped}:  # usually all three are the same string: scan each distinct form once

        hits |= _signatures(form)

    if hits:

        fail("INJECTION_DETECTED", "Suspected prompt injection", {"signatures": sorted(hits)})

    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in _CONTROL.sub("", stripped).split("\n")]

    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

    cleaned = re.sub(r" +([?.!,;:])", r"\1", cleaned)  # tidy gaps left where inline tags were removed

    if len(cleaned) > max_len:

        if reject_too_long:

            fail("INPUT_TOO_LONG", "Text exceeds the length limit", {"max_len": max_len})

        cleaned = cleaned[:max_len]

    return cleaned
 
 
@trace(logger, redact=_rt)

def safe_text(text, max_len=1000):

    """Like sanitize_free_text but never fails: returns a placeholder if an injection is found."""

    try:

        return sanitize_free_text(text, max_len=max_len)

    except ValueError:

        return REMOVED
 
 
@trace(logger, redact=_r)

def sanitize_tool_output(obj):

    """Sanitise every string in a tool/resource result (retrieved content is untrusted input).

    Returns (clean_object, findings); strings carrying an injection are replaced by a placeholder."""

    findings = []

    limit = load_config()["text"]["tool_output_max_len"]
 
    def walk(value, path):

        if isinstance(value, str):

            try:

                return sanitize_free_text(value, max_len=limit)

            except ValueError:

                findings.append({"path": path})

                return REMOVED

        if isinstance(value, dict):

            return {k: walk(v, f"{path}.{k}") for k, v in value.items()}

        if isinstance(value, list):

            return [walk(v, f"{path}[{i}]") for i, v in enumerate(value)]

        return value
 
    return walk(obj, "$"), findings
 
 
_APOSTROPHES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'", "\u00b4": "'", "`": "'"})
 
 
@trace(logger, redact=_rt)

def _signal_text(text):

    """Normalise before phrase matching so typography cannot hide a signal: NFKC, zero-width characters removed, curly

    apostrophes made straight (\"can\u2019t\" must match can'?t), whitespace collapsed."""

    cleaned = _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", str(text))).translate(_APOSTROPHES)

    return re.sub(r"\s+", " ", cleaned)
 
 
@trace(logger, redact=_rt)

def _co_occurs(text, first, second, window):

    """True if pattern `first` and pattern `second` both appear within `window` characters of each other (either order)."""

    other = re.compile(second, re.I)

    for match in re.finditer(first, text, re.I):

        if other.search(text, max(0, match.start() - window), min(len(text), match.end() + window)):

            return True

    return False
 
 
@trace(logger, redact=_r)

def detect_escalation_category(text):

    """Scan the WHOLE message for hard-escalation signals (signals/escalation.yaml): strong phrases and co-occurrence

    rules, on normalised text. Returns a category or 'none'. This is the deterministic backstop to the Classifier LLM."""

    signals = load_yaml("signals/escalation.yaml")

    pairs = signals.get("pairs") or {}

    text = _signal_text(text)

    for category in signals["priority"]:

        if any(re.search(pattern, text, re.I) for pattern in signals[category]):

            return category

        if any(_co_occurs(text, rule["a"], rule["b"], int(rule.get("window", 80))) for rule in pairs.get(category, [])):

            return category

    return "none"
 
 
# --------------------------------------------------------------------------- output filtering

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
 
 
@trace(logger, redact=_rt)

def _numbers(text):

    return {m.group(0).replace(",", "").rstrip(".") for m in _NUMBER.finditer(text)}
 
 
@trace(logger, redact=_rt)

def _overclaims(draft, grounded_text):

    """Absolute wording ('guaranteed', 'never fails', 'no risk', ...; config output_filter.overclaim_patterns) that the grounded

    facts do not themselves contain. A draft may repeat such a phrase only if the facts it was built from say it."""

    patterns = (load_config().get("output_filter") or {}).get("overclaim_patterns") or []

    grounded = grounded_text.lower()

    found = set()

    for pattern in patterns:

        for match in re.finditer(pattern, draft, re.I):

            if match.group(0).lower() not in grounded:

                found.add(match.group(0).lower())

    return sorted(found)
 
 
@trace(logger, redact=_r)

def check_output(draft, grounded_text):

    """Validate a draft against the grounded context: no unmasked identifiers, no figures that are not in the facts."""

    issues = []

    patterns = load_config()["ids"]

    for kind in load_config()["masked_kinds"]:

        if re.search(r"\b" + patterns[kind].strip("^$") + r"\b", draft):

            issues.append({"type": "leaked_identifier", "detail": f"unmasked {kind} identifier"})

    unsupported = sorted(_numbers(draft) - _numbers(grounded_text))

    if unsupported:

        issues.append({"type": "unsupported_claim", "detail": f"figures not in the grounded facts: {unsupported}"})

    overclaims = _overclaims(draft, grounded_text)

    if overclaims:

        issues.append({"type": "overclaim", "detail": f"absolute or unsupported wording not found in the grounded facts: {overclaims}"})

    return {"ok": not issues, "issues": issues, "text": mask_identifiers_in_text(draft)}
 
 
# --------------------------------------------------------------------------- PII-safe logging

PII_KEYS = {"account_id", "customer_id", "policy_id", "phone", "email", "portfolio_id", "claim_id",

            "account_number", "policy_number", "portfolio_number", "full_name", "customer_name", "name", "display_name"}

FREE_TEXT_KEYS = {"message", "body", "customer_message", "customer_stated_intent", "text", "draft", "query", "grounded_text",

                  "summary", "retrieved_context_summary", "prior_interaction_summary", "excerpt", "payment_terms",

                  "customer_text", "response_summary", "clause_text", "description", "details"}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

_PHONE = re.compile(r"(?<![\w-])\+?\d{2,4}[\s-]?\d{3,5}[\s-]?\d{4,5}(?![\w-])")

_LONG_DIGITS = re.compile(r"\b\d{12,19}\b")

_ANY_ID = re.compile(r"\b(?:CUS|ACC|PORT|CLM)-\d{5}\b|\bPOL-[A-Z0-9]{2}-\d{5}\b")
 
 
def _fingerprint(text):

    return f"<free-text len={len(text)} sha256={hashlib.sha256(text.encode()).hexdigest()[:8]}>"
 
 
def _mask_value(key, value):

    if key == "email":

        local, _, domain = value.partition("@")

        return f"{local[:1]}***@{domain}" if domain else "***"

    if key == "phone":

        return "******" + value[-4:]

    if key in ("full_name", "customer_name", "name", "display_name"):

        return value[:1] + "***"

    return "****" + value[-4:]
 
 
def scrub_text(text):

    """Mask PII-shaped substrings inside free text (ids, e-mails, phone-like and long digit runs)."""

    text = _ANY_ID.sub(lambda m: "****" + m.group(0)[-4:], text)

    text = _EMAIL.sub(lambda m: m.group(0)[:1] + "***@" + m.group(0).split("@")[1], text)

    text = _PHONE.sub(lambda m: "******" + m.group(0)[-4:], text)

    return _LONG_DIGITS.sub(lambda m: "******" + m.group(0)[-4:], text)
 
 
def redact_for_logging(data):

    """Mask PII-shaped argument names (account_id, customer_id, policy_id, phone, email, ...) and scrub PII patterns in

    every string BEFORE anything reaches a log line. Data minimisation: logs keep the last four characters (enough to

    correlate events) and only the length + fingerprint of customer free text - never identities or message content."""

    if isinstance(data, dict):

        out = {}

        for key, value in data.items():

            if key in PII_KEYS and isinstance(value, str):

                out[key] = _mask_value(key, value)

            elif key in FREE_TEXT_KEYS and isinstance(value, str):

                out[key] = _fingerprint(value)

            elif key == "sql" and isinstance(value, str):

                out[key] = scrub_text(value)

            else:

                out[key] = redact_for_logging(value)

        return out

    if isinstance(data, (list, tuple)):

        return [redact_for_logging(v) for v in data]

    if isinstance(data, str):  # long strings are free text (messages, prompts, documents): fingerprint only

        return _fingerprint(data) if len(data) > 100 else scrub_text(data)

    return data
 
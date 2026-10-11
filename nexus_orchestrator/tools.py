

"""Tools handed to CrewAI agents: the LangChain knowledge search, and MCP tools from the live servers."""

import json

from contextlib import contextmanager
 
from config import load_config

from errors import parse_error

from guardrails import redact_for_logging, sanitize_free_text

from knowledge import demo_enabled, search

from logging_config import get_logger, trace

from nexus_orchestrator.mcp_client import server_params
 
logger = get_logger("agent_tools")

QUARANTINED = []  # documents blocked by the injection filter during the current request (the pipeline reads + audits them)

RETRIEVED = []  # sanitised passages the Retriever's knowledge search returned during the current request (rule-based conflict check)
 
 
def _r(data):

    return redact_for_logging(data)
 
 
@trace(logger, redact=_r)

def knowledge_passages(query):

    """LangChain semantic search; EVERY retrieved passage is sanitised, and poisoned ones are quarantined (spec 4)."""

    passages, withheld = [], 0

    for hit in search(query, include_demo=demo_enabled()):

        try:

            clean = sanitize_free_text(hit["text"], max_len=load_config()["text"]["tool_output_max_len"])

        except ValueError as exc:

            QUARANTINED.append({"doc_id": hit["doc_id"], "signatures": (parse_error(exc) or {}).get("details", {}).get("signatures", [])})

            withheld += 1

            continue

        passages.append({"source": f"kb:{hit['doc_id']}", "category": hit["category"], "text": clean})

        RETRIEVED.append({"source": f"kb:{hit['doc_id']}", "text": clean})

    return {"passages": passages, "withheld_by_safety_filter": withheld}
 
 
@trace(logger, redact=_r)

def make_search_tool():

    """The CrewAI tool wrapping knowledge_passages (imported lazily so non-agent code does not need crewai)."""

    from crewai.tools import tool
 
    @tool("search_knowledge")

    def search_knowledge(query: str) -> str:

        """Search Meridian's policy documents, product documents, FAQs and message templates. Input: the customer's question in plain words. Returns JSON passages, each with the source document id."""

        return json.dumps(knowledge_passages(query))
 
    return search_knowledge
 
 
@contextmanager

def mcp_tools(servers, only=None, bound_customer=None, max_scope=None):

    """Open the live MCP servers for an agent (CrewAI's MCPServerAdapter) and yield their tools, optionally filtered by name.

    With bound_customer / max_scope the servers started here refuse - in the server process itself - every record that belongs

    to another customer and every caller_scope above max_scope, whatever arguments the model chooses."""

    from crewai_tools import MCPServerAdapter

    with MCPServerAdapter([server_params(server, bound_customer, max_scope) for server in servers]) as tools:

        yield [tool for tool in tools if only is None or tool.name in only]
 
"""FastMCP registration for insurance_server (tools, resources, prompts) using the real `mcp` package."""
try:
    from mcp.server.fastmcp import FastMCP  # mcp 1.x (pinned in requirements.txt)
except ImportError:  # pragma: no cover - mcp 2.x renamed it; best effort, unverified
    from mcp.server.mcpserver import MCPServer as FastMCP

from insurance_server import tools
from guardrails import redact_for_logging
from logging_config import get_logger, trace

logger = get_logger("insurance_server")


def _r(data):
    return redact_for_logging(data)


@trace(logger, redact=_r)
def build_server():
    mcp = FastMCP("insurance_server")
    for fn in tools.TOOLS.values():
        mcp.tool()(fn)
    for template in tools.RESOURCE_TEMPLATES:
        mcp.resource(template)(tools.RESOURCES[template.split("://")[0]])
    for fn in tools.PROMPTS.values():
        mcp.prompt()(fn)
    return mcp

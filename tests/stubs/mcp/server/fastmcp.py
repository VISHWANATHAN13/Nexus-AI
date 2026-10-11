"""Minimal FastMCP look-alike: registries + async list_* methods shaped like the real ones."""
import inspect
from types import SimpleNamespace


class FastMCP:
    IS_STUB = True

    def __init__(self, name: str, **kwargs):
        self.name = name
        self._tools, self._resources, self._prompts = {}, {}, {}

    def tool(self, name=None, **kw):
        def deco(fn):
            self._tools[name or fn.__name__] = fn
            return fn
        return deco

    def resource(self, uri, **kw):
        def deco(fn):
            params = set(inspect.signature(fn).parameters)
            for p in __import__("re").findall(r"{(\w+)}", uri):
                assert p in params, f"URI parameter {p} missing from function signature"
            self._resources[uri] = fn
            return fn
        return deco

    def prompt(self, name=None, **kw):
        def deco(fn):
            self._prompts[name or fn.__name__] = fn
            return fn
        return deco

    async def list_tools(self):
        return [SimpleNamespace(name=n, description=f.__doc__) for n, f in self._tools.items()]

    async def list_resource_templates(self):
        return [SimpleNamespace(uriTemplate=u) for u in self._resources]

    async def list_prompts(self):
        return [SimpleNamespace(name=n, arguments=[SimpleNamespace(name=p) for p in inspect.signature(f).parameters])
                for n, f in self._prompts.items()]

    def run(self, *a, **k):
        raise RuntimeError("stub FastMCP cannot serve; install the real `mcp` package")

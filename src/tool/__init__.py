"""Tool package.

Nothing is imported eagerly: even the base types in ``.types`` reach into
``src.dynamic`` (which needs ``inflection``). Every name is resolved lazily
on first attribute access (PEP 562) so that importing one
tool — e.g. ``src.tool.workflow_tools.website_audit`` for the audit
service — doesn't drag in every other tool's heavy dependency tree
(browser automation, ``src.dynamic``/``asyncio_atexit``, ESG retrievers,
the TCP server, ...). Same pattern as ``src/review/__init__.py``,
``src/environment/__init__.py`` and ``src/utils/__init__.py``.

``from src.tool import X`` still works: it goes through ``__getattr__``.
"""
import importlib
import sys

_LAZY = {
    "Tool": ".types",
    "ToolResponse": ".types",
    "ToolContextManager": ".context",
    "WebFetcherTool": ".default_tools",
    "WebSearcherTool": ".default_tools",
    "MdifyTool": ".default_tools",
    "DoneTool": ".default_tools",
    "TodoTool": ".default_tools",
    "PythonInterpreterTool": ".default_tools",
    "BashTool": ".default_tools",
    "BrowserTool": ".workflow_tools",
    "DeepResearcherTool": ".workflow_tools",
    "DeepAnalyzerTool": ".workflow_tools",
    "RetrieverTool": ".esg_tools",
    "PlotterTool": ".esg_tools",
    "ReformulatorTool": ".other_tools",
    "TCPServer": ".server",
    "tcp": ".server",
}

__all__ = list(_LAZY)


def __getattr__(name: str):
    try:
        module_name = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(importlib.import_module(module_name, __name__), name)
    # Cache on the package so later lookups (and monkeypatching) hit a real attribute.
    setattr(sys.modules[__name__], name, value)
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))

"""Lazy package init — see src.review.__init__ for the same rationale.

Each environment class here (trading exchanges, FAISS, GitHub, mobile
automation, ...) has its own heavy, independent dependency chain. A caller
that only needs one of them (e.g. src.audit.browser_session needs
src.environment.browser.Browser, imported directly as a submodule) shouldn't
be forced to have every other environment's dependencies installed just
because Python runs this __init__.py on any submodule import.

Public names are unchanged; each is loaded on first actual access via
module-level __getattr__ (PEP 562) instead of at package-import time.
"""

_LAZY_ATTRS = {
    "FileSystemEnvironment": ".file_system_environment",
    "GitHubEnvironment": ".github_environment",
    "InterdayTradingEnvironment": ".interday_trading_environment",
    "IntradayTradingEnvironment": ".intraday_trading_environment",
    "DatabaseEnvironment": ".database_environment",
    "FaissEnvironment": ".faiss_environment",
    "OperatorBrowserEnvironment": ".operator_browser_environment",
    "MobileEnvironment": ".mobile_environment",
    "AnthropicMobileEnvironment": ".anthropic_mobile_environment",
    "AlpacaEnvironment": ".alpaca_environment",
    "BinanceEnvironment": ".binance_environment",
    "OnlineHyperliquidEnvironment": ".hyperliquid_environment",
    "OfflineHyperliquidEnvironment": ".hyperliquid_environment",
    "ecp": ".server",
}

__all__ = list(_LAZY_ATTRS)


def __getattr__(name):
    module_name = _LAZY_ATTRS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_name, __name__)
    return getattr(module, name)

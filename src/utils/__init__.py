"""Lazy package init — see src.review.__init__ for the same rationale.

This is a grab-bag of utilities from the repo's original ESG/trading-agent
scope: file/path helpers alongside trading-record dataclasses, a Hugging Face
Hub uploader, a cairosvg-based screenshot helper, etc. A caller that only
needs e.g. Singleton (as src.logger does) shouldn't need every other
utility's dependencies (pandas, torch, huggingface_hub, cairosvg, ...)
installed just to import this package.

Public names are unchanged; each is loaded on first actual access via
module-level __getattr__ (PEP 562) instead of at package-import time.
ScreenshotService keeps its special case (None when the cairo system lib
isn't available) but only evaluated lazily now, not at every import of
src.utils.
"""

_LAZY_ATTRS = {
    "get_project_root": ".path_utils",
    "assemble_project_path": ".path_utils",
    "Singleton": ".singleton",
    "_is_package_available": ".utils",
    "encode_file_base64": ".utils",
    "decode_file_base64": ".utils",
    "make_file_url": ".utils",
    "parse_json_blob": ".utils",
    "truncate_content": ".utils",
    "truncate_dict": ".utils",
    "truncate_file_url": ".utils",
    "gather_with_concurrency": ".utils",
    "Record": ".record_utils",
    "TradingRecords": ".record_utils",
    "PortfolioRecords": ".record_utils",
    "get_token_count": ".token_utils",
    "TimeLevel": ".calender_utils",
    "TimeLevelFormat": ".calender_utils",
    "get_start_end_timestamp": ".calender_utils",
    "calculate_time_info": ".calender_utils",
    "get_standard_timestamp": ".calender_utils",
    "extract_boxed_content": ".string_utils",
    "dedent": ".string_utils",
    "get_world_size": ".misc",
    "get_rank": ".misc",
    "get_tag_name": ".name_utils",
    "get_newspage_name": ".name_utils",
    "get_md5": ".name_utils",
    "fetch_url": ".url_utils",
    "get_file_info": ".file_utils",
    "file_lock": ".file_utils",
    "get_env": ".env_utils",
    "get_jsonparsed_data": ".download_utils",
    "generate_intervals": ".download_utils",
    "push_to_hub_folder": ".hub_utils",
}

__all__ = list(_LAZY_ATTRS) + ["ScreenshotService"]


def __getattr__(name):
    if name == "ScreenshotService":
        try:
            from .screenshot_utils import ScreenshotService as _ScreenshotService
        except OSError:
            # cairo library not available (expected on some systems)
            return None
        return _ScreenshotService

    module_name = _LAZY_ATTRS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_name, __name__)
    return getattr(module, name)

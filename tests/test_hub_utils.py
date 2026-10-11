"""CI went red on `main` because huggingface_hub is not pinned: a fresh install
resolved to 2.2.0, which removed upload_large_folder (the top-level function AND
the HfApi method). hub_utils imported it at module level, so `import src.utils`
failed, and with it everything that imports src.logger - the whole test suite
and the service - even though only the (unused here) hub-push helper needs it."""

import ast
import os
import sys
import types

from src.utils import hub_utils


def test_no_module_level_import_of_the_removed_hub_function():
    path = hub_utils.__file__
    tree = ast.parse(open(path, encoding="utf-8").read())
    top_level_hub_names = [
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "huggingface_hub"
        for alias in node.names
    ]
    assert "upload_large_folder" not in top_level_hub_names, "import it lazily - hub 2.x removed it"


def _fake_hub(**attrs):
    module = types.ModuleType("huggingface_hub")
    for name, value in attrs.items():
        setattr(module, name, value)
    return module


class _Api:
    def __init__(self):
        self.calls = []

    def upload_folder(self, **kwargs):
        self.calls.append(kwargs)


def test_falls_back_to_upload_folder_when_the_hub_has_no_upload_large_folder(monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", _fake_hub())  # hub 2.x: nothing to import
    api = _Api()
    hub_utils._upload_folder(api, repo_id="u/r", folder_path="/tmp/x", repo_type="dataset", ignore_patterns=["*.log"])
    assert api.calls == [{"repo_id": "u/r", "folder_path": "/tmp/x", "revision": "main", "ignore_patterns": ["*.log"], "repo_type": "dataset"}]


def test_uses_upload_large_folder_when_the_hub_still_has_it(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "huggingface_hub", _fake_hub(upload_large_folder=lambda **kw: calls.append(kw)))
    api = _Api()
    hub_utils._upload_folder(api, repo_id="u/r", folder_path="/tmp/x", repo_type="dataset", ignore_patterns=["*.log"])
    assert api.calls == [] and calls[0]["num_workers"] == 4 and calls[0]["repo_id"] == "u/r"

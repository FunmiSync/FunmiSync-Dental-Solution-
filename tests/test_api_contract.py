"""Offline compatibility guardrails, using only Python's standard library."""
import ast
import json
from pathlib import Path
import unittest

from tools.api_contract import ROOT, capture_contract, inspect_routes


class ApiContractTests(unittest.TestCase):
    def test_existing_public_contract_is_preserved(self):
        expected = json.loads((ROOT / "docs/api_contract_baseline.json").read_text())
        # Reviewed Phase 2 exception: the original None limit crashed on limit + 1.
        # Retain the original snapshot and constrain this exception to one field.
        route = next(item for item in expected["routes"]
                     if item["method"] == "GET" and item["path"] == "/dsos/{dso_id}/sync-logs")
        limit = next(item for item in route["parameters"] if item["name"] == "limit")
        self.assertEqual(limit["default"], "Query(default=None)")
        limit["default"] = "Query(default=50, ge=1, le=200)"
        expected["routes"].append({"method":"GET","path":"/health/ready","options":{},"parameters":[]})
        expected["routes"].sort(key=lambda item:(item["path"],item["method"]))
        self.assertEqual(expected, capture_contract(),
                         "API surface changed. Review frontend compatibility; do not blindly replace the baseline.")

    def test_no_duplicate_method_path_pairs(self):
        routes = capture_contract()["routes"]
        keys = [(route["method"], route["path"]) for route in routes]
        self.assertEqual(len(keys), len(set(keys)))

    def test_all_python_sources_parse(self):
        for directory in ("app", "api", "auth", "billing", "caches", "core", "infra", "sdk", "sdk for crms", "workers", "alembic", "tools", "tests"):
            for path in (ROOT / directory).rglob("*.py"):
                with self.subTest(file=str(path.relative_to(ROOT))):
                    ast.parse(path.read_text(), filename=str(path))
        for name in ("main.py", "config.py"):
            ast.parse((ROOT / name).read_text())

    def test_inspector_detects_contract_changes(self):
        original = '''
@router.post("/wallet", status_code=201, response_model=WalletOut)
async def create(payload: WalletIn, key: str = Header(..., alias="Idempotency-Key")):
    pass
'''
        variants = (
            original.replace('"/wallet"', '"/wallets"'),
            original.replace("status_code=201", "status_code=200"),
            original.replace("WalletOut", "ChangedOut"),
            original.replace("WalletIn", "ChangedIn"),
            original.replace("Idempotency-Key", "Other-Key"),
        )
        baseline = inspect_routes(original, "router", "/toroforge")
        for variant in variants:
            self.assertNotEqual(baseline, inspect_routes(variant, "router", "/toroforge"))

    def test_inspector_ignores_handler_rename(self):
        original = '@router.get("/health")\ndef old_name():\n    pass\n'
        renamed = original.replace("old_name", "new_name")
        self.assertEqual(inspect_routes(original, "router"), inspect_routes(renamed, "router"))


if __name__ == "__main__":
    unittest.main()

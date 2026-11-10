"""Verify the risks introduced by package moves, without live service access."""
import ast
import hashlib
import importlib
import json
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from runtime_support import configure_test_environment
from tools.api_contract import ROOT, capture_contract

configure_test_environment()

MODULES = json.loads((ROOT / "docs/mvc_module_map.json").read_text())
FINGERPRINTS = json.loads((ROOT / "docs/mvc_implementation_baseline.json").read_text())
PHASE_4_CHANGES = json.loads((ROOT / "docs/phase_4_changed_modules.json").read_text())
PHASE_5_CHANGES = json.loads((ROOT / "docs/phase_5_changed_modules.json").read_text())
INTENTIONAL_CHANGES = {**PHASE_4_CHANGES, **PHASE_5_CHANGES}
LEGACY_ROOTS = {"core", "auth", "api", "infra", "billing", "caches", "workers", "sdk", "config"}
SESSION_METHODS = {"query", "add", "add_all", "commit", "flush", "rollback", "refresh", "execute", "delete", "merge"}
_guards = []


def setUpModule():
    for target in ("sqlalchemy.engine.Engine.connect", "redis.Redis.execute_command",
                   "redis.asyncio.Redis.execute_command", "httpx.AsyncClient.send"):
        guard = patch(target, side_effect=AssertionError("Live service access is forbidden in MVC tests"))
        guard.start()
        _guards.append(guard)


def tearDownModule():
    for guard in reversed(_guards):
        guard.stop()


class ImportCompatibilityTests(unittest.TestCase):
    def test_every_legacy_module_is_the_canonical_module(self):
        for old, new in MODULES.items():
            with self.subTest(old=old, new=new):
                self.assertIs(importlib.import_module(old), importlib.import_module(new))

    def test_old_patch_path_changes_the_globals_used_by_new_controller(self):
        from app.controllers.auth import login
        replacement = MagicMock(name="replacement_session")
        with patch("auth.login.start_login_session", replacement):
            self.assertIs(login.start_login_session, replacement)
            self.assertIs(login.login.__globals__["start_login_session"], replacement)

    def test_cold_import_orders_have_single_model_and_worker_identities(self):
        # Test in fresh interpreters: an already populated sys.modules can hide cycles.
        script = '''
import importlib, json, sys
from unittest.mock import patch
sys.path.insert(0, "tests")
from runtime_support import configure_test_environment
configure_test_environment()
pairs = json.loads(sys.argv[1])
with patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("No database")):
    for first, second in pairs:
        a = importlib.import_module(first)
        b = importlib.import_module(second)
        assert a is b, (first, second)
    from sqlalchemy.orm import configure_mappers
    configure_mappers()
    from main import app
    assert len(app.openapi()["paths"]) == 38
'''
        pairs = [("core.models", "app.models.entities"), ("auth.login", "app.controllers.auth.login"),
                 ("workers.workers", "app.infrastructure.workers.appointments")]
        for order in (pairs, [(new, old) for old, new in reversed(pairs)]):
            result = subprocess.run([sys.executable, "-B", "-c", script, json.dumps(order)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_and_new_rq_job_references_resolve_without_redis(self):
        from rq.job import Job
        from rq.utils import import_attribute
        from app.infrastructure.workers.appointments import process_crm_load_job
        connection = MagicMock()
        connection.execute_command.side_effect = AssertionError("No Redis commands")
        for module in ("workers.workers", "app.infrastructure.workers.appointments"):
            path = module + ".process_crm_load_job"
            self.assertIs(import_attribute(path), process_crm_load_job)
            original = Job.create(path, args=(uuid4(), "ghl", {}, uuid4()), connection=connection)
            restored = Job(connection=connection)
            restored.data = original.data
            self.assertIs(restored.func, process_crm_load_job)
            self.assertEqual(restored.args, original.args)
        connection.execute_command.assert_not_called()


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_canonical_app_never_imports_legacy_packages(self):
        for path in (ROOT / "app").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0], LEGACY_ROOTS, str(path))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], LEGACY_ROOTS, str(path))

    def test_non_controller_layers_do_not_import_controllers(self):
        for path in (ROOT / "app").rglob("*.py"):
            if "controllers" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(node.module.startswith("app.controllers"), str(path))

    def test_moved_implementations_preserve_non_import_ast(self):
        # Separate source relocation from workflow edits. Later phases can deliberately
        # replace this migration-only invariant with focused behavior tests.
        class RemoveImports(ast.NodeTransformer):
            def visit_Import(self, node):
                return None

            def visit_ImportFrom(self, node):
                return None

        for new, expected in FINGERPRINTS["non_import_ast_sha256"].items():
            if new in INTENTIONAL_CHANGES:
                self.assertGreater(len(INTENTIONAL_CHANGES[new]), 20)
                continue
            with self.subTest(module=new):
                after = ast.parse((ROOT / (new.replace(".", "/") + ".py")).read_text())
                actual = hashlib.sha256(ast.dump(RemoveImports().visit(after)).encode()).hexdigest()
                self.assertEqual(expected, actual)

    def test_recorded_changes_name_real_modules(self):
        # A misspelled entry would silently switch off a fingerprint check.
        for name in INTENTIONAL_CHANGES:
            self.assertTrue((ROOT / (name.replace(".", "/") + ".py")).exists(), name)

    def test_original_offline_contract_inspection_follows_aliases(self):
        captured = capture_contract()
        self.assertEqual(len(captured["routes"]), 38)
        self.assertEqual(len(captured["schemas"]), 61)
        self.assertEqual(len(captured["cookies"]), 3)


def _own_nodes(function):
    # The function's own body, excluding nested functions and classes.
    stack = list(function.body)
    while stack:
        node = stack.pop()
        yield node
        stack.extend(child for child in ast.iter_child_nodes(node)
                     if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)))


def _defines_router(path):
    return any(isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "router" for t in node.targets)
               for node in ast.parse(path.read_text()).body)


class LayerRuleTests(unittest.TestCase):
    """Controllers handle HTTP, services own rules and transactions."""

    def test_controllers_leave_database_work_to_services(self):
        for path in (ROOT / "app/controllers").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "db"
                        and node.func.attr in SESSION_METHODS):
                    self.fail(f"{path.relative_to(ROOT)}:{node.lineno} calls db.{node.func.attr}(); move it into a service")

    def test_services_raise_domain_errors_not_http_errors(self):
        for path in (ROOT / "app/services").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(("fastapi", "starlette")):
                    names = {alias.name for alias in node.names}
                    self.assertNotIn("HTTPException", names, f"{path.relative_to(ROOT)}: raise app.services.errors instead")

    def test_async_functions_actually_wait_on_something(self):
        # An `async def` that never awaits runs on the shared event loop, so its database and
        # cache calls freeze every other request. Write plain `def`: FastAPI runs those routes
        # in a thread pool, and callers no longer `await` something that cannot pause.
        allowed = {("app/controllers/errors.py", "service_error_handler")}  # builds a response, no I/O
        for path in (ROOT / "app").rglob("*.py"):
            relative = str(path.relative_to(ROOT))
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.AsyncFunctionDef) or (relative, node.name) in allowed:
                    continue
                waits = any(isinstance(n, (ast.Await, ast.AsyncWith, ast.AsyncFor)) for n in _own_nodes(node))
                self.assertTrue(waits, f"{relative}:{node.lineno} {node.name} is async but never awaits; make it def")

    def test_app_code_uses_only_the_async_session(self):
        # The API and worker run on AsyncSession (asyncpg). Only alembic/ and tools/ keep a sync session.
        for path in (ROOT / "app").rglob("*.py"):
            relative = str(path.relative_to(ROOT))
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy.orm":
                    self.assertNotIn("Session", {alias.name for alias in node.names}, f"{relative}: use AsyncSession")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "query":
                    self.fail(f"{relative}:{node.lineno} uses the legacy Query API; use select() with the async session")

    def test_session_calls_are_awaited(self):
        # A missing await on commit()/execute() silently does nothing; this catches it statically.
        for path in (ROOT / "app").rglob("*.py"):
            tree = ast.parse(path.read_text())
            awaited = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Await)}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in SESSION_METHODS - {"add", "add_all", "query"}
                        and ast.unparse(node.func.value).split(".")[-1] == "db"):
                    self.assertIn(id(node), awaited, f"{path.relative_to(ROOT)}:{node.lineno} {ast.unparse(node)} is not awaited")

    def test_controllers_import_shared_helpers_not_other_routers(self):
        for path in (ROOT / "app/controllers").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if not (isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app.controllers")):
                    continue
                base = ROOT / node.module.replace(".", "/")
                targets = [base.with_suffix(".py")] if base.with_suffix(".py").exists() else \
                          [base / f"{alias.name}.py" for alias in node.names]
                for target in targets:
                    self.assertFalse(_defines_router(target),
                                     f"{path.relative_to(ROOT)} imports router module {target.relative_to(ROOT)}")


class ServiceErrorTranslationTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_domain_error_keeps_its_status_and_detail_shape(self):
        from app.controllers.errors import service_error_handler
        from app.services import errors
        from app.services.registration import RegistrationConflict
        expected = {errors.InvalidRequest: 400, RegistrationConflict: 400, errors.AuthenticationFailed: 401,
                    errors.PermissionDenied: 403, errors.NotFound: 404, errors.Conflict: 409, errors.Gone: 410,
                    errors.TooManyRequests: 429, errors.OperationFailed: 500, errors.ServiceError: 500}
        for error_type, status_code in expected.items():
            with self.subTest(error=error_type.__name__):
                response = await service_error_handler(None, error_type("Readable message"))
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(json.loads(response.body), {"detail": "Readable message"})

    def test_application_registers_the_translator(self):
        from main import app
        from app.services.errors import ServiceError
        from app.controllers.errors import service_error_handler
        self.assertIs(app.exception_handlers[ServiceError], service_error_handler)


class ModelMetadataTests(unittest.TestCase):
    def test_model_identity_and_metadata_are_shared(self):
        from app.models.entities import Base, Users
        from app.infrastructure.database import Base as DatabaseBase
        from core.models import Users as LegacyUsers
        from core.database import Base as LegacyBase
        from sqlalchemy.orm import configure_mappers
        configure_mappers()
        self.assertIs(Users, LegacyUsers)
        self.assertIs(Base, LegacyBase)
        self.assertIs(Base, DatabaseBase)
        self.assertIs(Users.__table__, Base.metadata.tables["users"])
        self.assertEqual(len(Base.registry.mappers), 19)

    def test_postgres_ddl_changes_are_limited_to_reviewed_phase_4_tables(self):
        from app.models.entities import Base
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateTable, CreateIndex
        actual = {}
        for table in Base.metadata.sorted_tables:
            actual[table.name] = {
                "ddl": str(CreateTable(table).compile(dialect=postgresql.dialect())),
                "indexes": sorted(str(CreateIndex(index).compile(dialect=postgresql.dialect()))
                                  for index in table.indexes),
            }
        expected = json.loads((ROOT / "docs/database_metadata_baseline.json").read_text())
        changes = json.loads((ROOT / "docs/phase_4_metadata_changes.json").read_text())
        self.assertEqual(set(changes), {"wallets", "role_assignments", "audit_logs", "external_operations"})
        expected.update(changes)
        self.assertEqual(expected, actual)

    def test_alembic_uses_canonical_metadata_without_changing_revisions(self):
        tree = ast.parse((ROOT / "alembic/env.py").read_text())
        self.assertTrue(any(isinstance(node, ast.ImportFrom) and node.module == "app.models.entities"
                            and any(alias.name == "Base" for alias in node.names) for node in tree.body))
        for name, expected in FINGERPRINTS["revision_sha256"].items():
            self.assertEqual(expected, hashlib.sha256((ROOT / name).read_bytes()).hexdigest())

"""Inspect the public API without importing the app or connecting to services.

Run: python3 tools/api_contract.py
The checked-in baseline is deliberate: do not regenerate it to hide a breaking change.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def expression(node: ast.AST | None):
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return ast.unparse(node)


def keywords(call: ast.Call) -> dict:
    return {item.arg: expression(item.value) for item in call.keywords if item.arg}


def module_source(root: Path, module: str, seen: set[str] | None = None) -> str:
    """Follow a static compatibility alias without importing application code."""
    seen = set() if seen is None else seen
    if module in seen:
        raise ValueError(f"Circular module compatibility alias: {module}")
    seen.add(module)
    path = root / (module.replace(".", "/") + ".py")
    source = path.read_text()
    for node in ast.parse(source).body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.asname == "_implementation":
                    return module_source(root, f"{node.module}.{alias.name}", seen)
    return source


def inspect_routes(source: str, receiver: str, prefix: str = "") -> list[dict]:
    routes = []
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == receiver
                and decorator.func.attr in METHODS
            ):
                continue
            options = keywords(decorator)
            path = expression(decorator.args[0]) if decorator.args else options.get("path", "")
            args = node.args.posonlyargs + node.args.args
            defaults = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
            args += node.args.kwonlyargs
            defaults += list(node.args.kw_defaults)
            parameters = []
            for arg, default in zip(args, defaults):
                annotation = expression(arg.annotation)
                # Request/Response and DB sessions are implementation details.
                # Security dependencies remain recorded below.
                dependency = None
                if isinstance(default, ast.Call):
                    function = ast.unparse(default.func)
                    if function == "Depends":
                        dependency = expression(default.args[0]) if default.args else None
                        if dependency != "get_current_user":
                            continue
                if annotation in {"Request", "Response", "Session"}:
                    continue
                item = {"name": arg.arg, "type": annotation}
                if default is not None:
                    item["default"] = expression(default)
                if dependency:
                    item["dependency"] = dependency
                parameters.append(item)
            routes.append({
                "method": decorator.func.attr.upper(),
                "path": prefix + path,
                "options": {key: options[key] for key in (
                    "status_code", "response_model", "response_model_exclude_none",
                ) if key in options},
                "parameters": parameters,
            })
    return routes


def capture_contract(root: Path = ROOT) -> dict:
    main_source = (root / "main.py").read_text()
    main = ast.parse(main_source)
    imports = {}
    for node in main.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    routes = inspect_routes(main_source, "app")
    for node in main.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Attribute) or ast.unparse(call.func) != "app.include_router":
            continue
        # Current app mounts module.router directly. Fail visibly if this changes.
        module_name = ast.unparse(call.args[0]).removesuffix(".router")
        module = imports[module_name]
        source = module_source(root, module)
        prefix = ""
        for assignment in ast.parse(source).body:
            if isinstance(assignment, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "router" for target in assignment.targets
            ) and isinstance(assignment.value, ast.Call):
                prefix = keywords(assignment.value).get("prefix", "")
        routes.extend(inspect_routes(source, "router", prefix))

    schemas = {}
    for node in ast.parse(module_source(root, "core.schemas")).body:
        if not isinstance(node, ast.ClassDef):
            continue
        fields = {}
        for field in node.body:
            if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                required = field.value is None
                if isinstance(field.value, ast.Call) and ast.unparse(field.value.func) == "Field":
                    options = keywords(field.value)
                    required = not field.value.args and "default" not in options and "default_factory" not in options
                    if field.value.args:
                        required = isinstance(field.value.args[0], ast.Constant) and field.value.args[0].value is Ellipsis
                item = {"type": expression(field.annotation), "required": required}
                if field.value is not None:
                    item["default"] = expression(field.value)
                fields[field.target.id] = item
        schemas[node.name] = {"bases": [expression(base) for base in node.bases], "fields": fields}

    cookies = []
    for path in sorted((root / "auth").glob("*.py")):
        module = ".".join(path.relative_to(root).with_suffix("").parts)
        tree = ast.parse(module_source(root, module))
        constants = {
            target.id: expression(node.value)
            for node in tree.body if isinstance(node, ast.Assign)
            for target in node.targets if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "set_cookie":
                options = keywords(node)
                key = options.get("key")
                cookies.append({
                    "name": constants.get(key, key),
                    **{name: options[name] for name in ("httponly", "secure", "samesite", "path", "max_age") if name in options},
                })
    cors = {}
    for node in main.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "origins" for t in node.targets):
            cors["origins"] = expression(node.value)
            if cors["origins"] == "settings.cors_origins":
                # Inspect the declared default without loading environment secrets.
                config = ast.parse(module_source(root, "config"))
                settings_class = next(item for item in config.body if isinstance(item, ast.ClassDef) and item.name == "Settings")
                field = next(item for item in settings_class.body if isinstance(item, ast.AnnAssign) and item.target.id == "cors_origins")
                cors["origins"] = expression(field.value)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if call.args and expression(call.args[0]) == "CORSMiddleware":
                cors.update(keywords(call))
    return {
        "format_version": 1,
        "routes": sorted(routes, key=lambda item: (item["path"], item["method"])),
        "schemas": schemas,
        "cookies": sorted(cookies, key=lambda item: item["name"]),
        "cors": cors,
    }


if __name__ == "__main__":
    print(json.dumps(capture_contract(), indent=2, sort_keys=True))

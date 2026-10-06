from cryo.api.app import create_app
from cryo.config import Settings


def _routes(app):
    found = []

    def walk(router):
        for route in getattr(router, "routes", []):
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", set())
            endpoint = getattr(route, "endpoint", None)
            if path and methods:
                found.append((path, methods, endpoint))
            child = getattr(route, "original_router", None)
            if child is not None:
                walk(child)

    for route in app.routes:
        child = getattr(route, "original_router", None)
        if child is not None:
            walk(child)
        elif getattr(route, "path", None):
            found.append(
                (route.path, getattr(route, "methods", set()), getattr(route, "endpoint", None))
            )
    return found


def test_scan_cost_route_is_not_shadowed_by_the_dashboard_stub() -> None:
    app = create_app(Settings(_env_file=None, environment="test"))
    routes = _routes(app)
    scan = [
        endpoint
        for path, methods, endpoint in routes
        if path == "/api/v1/cost-attribution" and "GET" in methods
    ]
    stub = [
        endpoint
        for path, methods, endpoint in routes
        if path == "/api/v1/dashboards/cost-attribution" and "GET" in methods
    ]
    assert len(scan) == 1
    assert scan[0].__module__ == "cryo.api.routes_cost_attribution"
    assert len(stub) == 1
    assert stub[0].__module__ == "cryo.api.dashboard_routes"


def test_unsaved_provider_test_is_not_captured_by_the_id_route() -> None:
    app = create_app(Settings(_env_file=None, environment="test"))
    paths = [
        path
        for path, methods, _endpoint in _routes(app)
        if "POST" in methods and path.startswith("/api/v1/ai/providers/")
    ]
    assert paths.index("/api/v1/ai/providers/test") < paths.index("/api/v1/ai/providers/{provider_id}/test")

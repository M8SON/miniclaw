"""pytest configuration — repo root on sys.path + test-only module shims."""

import importlib.util
import sys
import types
from pathlib import Path


_REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(_REPO_ROOT))


def _register(dotted_name: str, file_path: Path) -> None:
    """Register a module by dotted name using its source file path.

    Used to keep legacy test imports like `containers.dashboard.eonet` working
    after the agentskills.io migration moved those files to
    `skills/<name>/scripts/`. Creates intermediate package shells as needed.
    """
    if not file_path.exists():
        return
    parts = dotted_name.split(".")
    for i in range(1, len(parts)):
        pkg_name = ".".join(parts[:i])
        if pkg_name not in sys.modules:
            pkg = types.ModuleType(pkg_name)
            pkg.__path__ = []  # mark as a namespace package
            sys.modules[pkg_name] = pkg

    spec = importlib.util.spec_from_file_location(dotted_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[dotted_name] = module
    spec.loader.exec_module(module)


# Shims for moved dashboard modules. After migration these live under
# skills/dashboard/scripts/, but tests still import them by the old dotted
# paths. Registering here keeps the test suite intact without touching the
# test files.
_dashboard_scripts = _REPO_ROOT / "skills" / "dashboard" / "scripts"
_register("containers.dashboard.dashboard_defaults", _dashboard_scripts / "dashboard_defaults.py")
_register("containers.dashboard.eonet", _dashboard_scripts / "eonet.py")
_register("containers.dashboard.app", _dashboard_scripts / "app.py")

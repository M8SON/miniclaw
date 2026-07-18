"""Guard: load_dotenv() must run before core.profiling is imported, or
KAIZEN_PROFILE in .env is read too late and profiling stays disabled."""

import re
from pathlib import Path


def test_load_dotenv_precedes_core_profiling_import():
    src = (Path(__file__).parent.parent / "main.py").read_text()
    dotenv_call = re.search(r"^load_dotenv\(\)", src, re.MULTILINE)
    profiling_import = re.search(
        r"^from core import profiling", src, re.MULTILINE
    )
    assert dotenv_call is not None, "load_dotenv() call not found in main.py"
    assert profiling_import is not None, "core.profiling import not found"
    assert dotenv_call.start() < profiling_import.start(), (
        "load_dotenv() must be called before `from core import profiling` "
        "so KAIZEN_PROFILE from .env is visible at profiling import time"
    )

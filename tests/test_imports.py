"""Each entry point imports on its own: an import cycle can hide in the test run (modules already loaded)."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["peace.worker", "peace.api", "peace.journal", "peace.phone", "peace.answers"])
def test_entry_points_import_in_a_fresh_interpreter(module):
    proc = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-800:]

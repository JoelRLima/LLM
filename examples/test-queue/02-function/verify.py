"""Independent behavior checks; exit nonzero on failure."""
import runpy
import sys
from pathlib import Path

workspace = Path(sys.argv[1])
add = runpy.run_path(str(workspace / "calculator.py"))["add"]
assert add(2, 3) == 5
assert add(-2, 3) == 1
assert add(0, 0) == 0
print("3 behavior checks passed")

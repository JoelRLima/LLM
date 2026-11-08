"""Bootstrap local Workbench maintenance without importing an old installation."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from llm_agent.interfaces.workbench.maintenance import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

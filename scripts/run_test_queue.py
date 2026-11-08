"""Checkout entry point; the installed package also supports -m llm_agent.interfaces.test_queue."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from llm_agent.interfaces.test_queue import main  # noqa: E402

if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments[:1] == ["queue"]:
        arguments = arguments[1:]
    raise SystemExit(main(arguments))

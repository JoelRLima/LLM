"""Compatibility entry point for the packaged benchmark command."""

from scripts.benchmark import main

__all__ = ["main"]

if __name__ == "__main__":
    main()

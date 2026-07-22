"""Compatibility entry point for the packaged terminal interface."""

from agent.interfaces.cli.app import main

__all__ = ["main"]

if __name__ == "__main__":
    main()

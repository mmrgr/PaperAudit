"""Backward-compatible alias for the PaperRevamper control panel."""

from paperrevamper.panel import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())

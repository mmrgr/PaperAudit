"""Backward-compatible alias for the PaperAudit control panel."""

from paperaudit.panel import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())

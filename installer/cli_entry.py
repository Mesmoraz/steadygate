"""PyInstaller entry point for the standalone ``steadygate`` CLI."""

from steadygate.cli import main

raise SystemExit(main())

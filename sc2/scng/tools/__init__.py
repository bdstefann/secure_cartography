"""Cross-cutting tools that live above scng.discovery and scng.creds.

Modules here drive devices for purposes other than topology discovery —
mass config push, audit collection, etc. — and are deliberately shared
between the CLI scripts under scripts/ and the PyQt6 GUI under sc2/ui.
"""

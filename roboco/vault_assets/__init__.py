"""Packaged Obsidian-vault template assets (.obsidian/ config + _meta/ dashboards).

Static, non-Python files only. Copied into the live vault by
``roboco.vault.ensure_vault_assets`` (first enable / rebuild); never imported
as code. Kept as a real package (not a loose repo-root dir) so
``importlib.resources`` resolves them regardless of how ``roboco`` is
installed/packaged.
"""

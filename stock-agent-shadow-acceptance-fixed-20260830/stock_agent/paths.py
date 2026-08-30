from __future__ import annotations

from pathlib import Path

# Repository root for an installed checkout or a source checkout.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPT_LIBRARY_DIRNAME = "STOCK_AGENT_OBSIDIAN_PROMPT_LIBRARY_V2_2"


def canonical_prompt_library_root() -> Path:
    """Return the repository's single canonical Prompt Library root.

    The production runtime and tests must resolve the same root. An
    outputs/ mirror is intentionally not consulted: it previously made CI
    and local execution select different libraries.
    """
    root = PROJECT_ROOT / PROMPT_LIBRARY_DIRNAME
    manifest = root / "prompt_registry_manifest_v2_2.json"
    if not manifest.is_file():
        raise RuntimeError(f"canonical Prompt Library is missing: {root}")
    return root


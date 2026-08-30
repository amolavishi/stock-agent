from __future__ import annotations

import unittest
from pathlib import Path

from stock_agent.paths import canonical_prompt_library_root


class PromptLibraryPathContractTests(unittest.TestCase):
    def test_canonical_root_is_repository_root_without_outputs_fallback(self) -> None:
        root = canonical_prompt_library_root()
        self.assertEqual(root.name, "STOCK_AGENT_OBSIDIAN_PROMPT_LIBRARY_V2_2")
        self.assertTrue((root / "prompt_registry_manifest_v2_2.json").is_file())
        self.assertNotEqual(root.parent.name, "outputs")
        self.assertEqual(root, Path(__file__).resolve().parents[1] / "STOCK_AGENT_OBSIDIAN_PROMPT_LIBRARY_V2_2")


if __name__ == "__main__":
    unittest.main()


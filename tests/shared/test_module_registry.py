from __future__ import annotations

import unittest
from pathlib import Path

from career_bridge.module_registry import Capability, module_inventory


class ModuleRegistryTests(unittest.TestCase):
    def test_all_requested_capabilities_are_registered_once(self) -> None:
        modules = module_inventory()
        self.assertEqual(len(modules), 10)
        self.assertEqual({item.capability for item in modules}, set(Capability))

    def test_registered_paths_exist(self) -> None:
        root = Path(__file__).resolve().parents[2]
        missing: list[str] = []
        for descriptor in module_inventory():
            for relative in descriptor.primary_paths:
                if not (root / relative).exists():
                    missing.append(relative)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()

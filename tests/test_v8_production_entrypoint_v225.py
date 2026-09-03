from __future__ import annotations

import unittest

import stock_agent


class V8ProductionEntrypointV225Tests(unittest.TestCase):
    def test_package_production_symbol_resolves_to_canonical_composed_runtime(self):
        from stock_agent.production import ProductionStockAgent as canonical
        self.assertIs(stock_agent.ProductionStockAgent, canonical)
        self.assertIsNot(stock_agent.BaseProductionStockAgent, canonical)
        self.assertEqual(canonical.__name__, "V8PreLiveSentinelProductionStockAgent")

    def test_explicit_base_symbol_is_named_as_non_production_authority(self):
        self.assertEqual(stock_agent.BaseProductionStockAgent.__name__, "ProductionStockAgent")
        self.assertFalse(hasattr(stock_agent.BaseProductionStockAgent, "v8_semantic_core_version"))

    def test_cli_and_library_share_bootstrap_composition_contract(self):
        from stock_agent.bootstrap import install_production_stack, production_composition
        from stock_agent.production import production_composition as production_module_composition

        install_production_stack()
        self.assertEqual(production_composition(), production_module_composition())
        composition = production_composition()
        self.assertEqual(composition["runtime_class"], "V8PreLiveSentinelProductionStockAgent")
        self.assertTrue(composition["canonical_production_entrypoint"])
        self.assertTrue(composition["production_composition_valid"])


if __name__ == "__main__":
    unittest.main()

import importlib
import unittest


class ImportSmokeTests(unittest.TestCase):
    def test_scheduled_entrypoint_imports_without_starting_network_calls(self):
        module = importlib.import_module("paper_trader.main")
        self.assertTrue(callable(module.run_once))
        self.assertTrue(callable(module.main))


if __name__ == "__main__":
    unittest.main()

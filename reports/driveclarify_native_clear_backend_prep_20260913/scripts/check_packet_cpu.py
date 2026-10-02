"""异机小包只执行自包含10项检查；本机外部源存在性另由完整14项和dry-run核验。"""
import argparse
import importlib
from pathlib import Path
import sys
import unittest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.source.resolve()))
module = importlib.import_module("experiments.driveclarify_native_clear_backend_dev_20260913.tests.test_preparation")
suite = unittest.TestSuite([
    unittest.defaultTestLoader.loadTestsFromTestCase(module.ObserverTests),
    module.InputsTests("test_two_full_native_geometric_tasks"),
    module.InputsTests("test_dry_script_has_only_stdlib_and_no_process_executor"),
])
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)

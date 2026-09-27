#!/usr/bin/env python3
"""Run the property tests.

They are ordinary unittest modules and run as part of the suite; this
runner exists so `tests/run-tests.sh --property` has one obvious command
and so they can be run alone while a property is being written.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromNames(
        ["tests.property.test_properties"])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)

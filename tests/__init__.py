"""xtm's test suite.

Bytecode goes under .build/ however the suite is started. tests/run-tests.sh
sets PYTHONPYCACHEPREFIX for every interpreter it starts; this covers a bare
`python3 -m unittest tests.x` as well, which otherwise leaves __pycache__
directories through src/ and tests/.
"""

import sys
from pathlib import Path

if getattr(sys, "pycache_prefix", "unset") is None:
    sys.pycache_prefix = str(Path(__file__).resolve().parent.parent / ".build" / "pycache")

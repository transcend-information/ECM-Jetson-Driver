"""Public installer entry; no test fixture or alternate operation dispatch."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ecm_env.check import require_system_python
require_system_python()
from ecm_install.entry import main

if __name__ == '__main__':
    raise SystemExit(main())

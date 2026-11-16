import os
import sys
import tempfile
from pathlib import Path

# The router derives every state path from the home directory at import, so the suite gets one of its own.
os.environ["HOME"] = tempfile.mkdtemp(prefix="statusline-test-home-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

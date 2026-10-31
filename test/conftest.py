import os
import tempfile

# The router derives every state path from the home directory at import, so the suite gets one of its own.
os.environ["HOME"] = tempfile.mkdtemp(prefix="statusline-test-home-")

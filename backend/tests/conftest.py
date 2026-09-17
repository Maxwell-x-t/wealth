import os
import tempfile

os.environ.setdefault("WEALTH_DATA_DIR", tempfile.mkdtemp(prefix="wealth-ledger-tests-"))
os.environ.setdefault("WEALTH_DISABLE_SYNC", "1")

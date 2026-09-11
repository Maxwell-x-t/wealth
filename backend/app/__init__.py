from pathlib import Path
import sys

# Both the web app and CLI execute the strategy source in this checkout.
strategy_root = str(Path(__file__).resolve().parents[2] / "strategy")
if strategy_root not in sys.path:
    sys.path.insert(0, strategy_root)

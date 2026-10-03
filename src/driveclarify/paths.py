"""Package paths work in editable checkouts and installed wheels."""
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
RESOURCES = PACKAGE_ROOT / "resources"


import json
import sys
from pathlib import Path

# Ensure repository/worktree root is first in sys.path
root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from app.main import app  # noqa: E402


def export_openapi():
    with open("openapi.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(app.openapi(), f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    export_openapi()

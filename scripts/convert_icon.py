from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtGui


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: convert_icon.py <source.webp> <target.ico>")

    source = Path(sys.argv[1]).resolve()
    target = Path(sys.argv[2]).resolve()

    if not source.exists():
        raise SystemExit(f"Missing source image: {source}")

    image = QtGui.QImage(str(source))
    if image.isNull():
        raise SystemExit(f"Failed to load image: {source}")

    target.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(target), "ICO"):
        raise SystemExit(f"Failed to write icon: {target}")

    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

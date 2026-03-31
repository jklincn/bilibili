from __future__ import annotations

import sys
from pathlib import Path

import py7zr


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if len(args) != 2:
        print("usage: python scripts/make_7z.py <source_dir> <archive_path>", file=sys.stderr)
        return 2

    source_dir = Path(args[0]).resolve()
    archive_path = Path(args[1]).resolve()

    if not source_dir.is_dir():
        print(f"source directory not found: {source_dir}", file=sys.stderr)
        return 1

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        archive_path.unlink()

    with py7zr.SevenZipFile(archive_path, "w") as archive:
        for child in sorted(source_dir.iterdir()):
            archive.writeall(child, arcname=child.name)

    print(f"created {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

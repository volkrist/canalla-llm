"""PyInstaller entry. Import the package so relative imports inside app work."""

from app.runtime_entry import main

if __name__ == "__main__":
    raise SystemExit(main())

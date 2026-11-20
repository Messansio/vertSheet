"""PyInstaller entry point of vertSheet.exe (window, uninstaller)."""
import sys

from vertsheet.app import main

if __name__ == "__main__":
    sys.exit(main())

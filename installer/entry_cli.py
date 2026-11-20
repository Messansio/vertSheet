"""PyInstaller entry point of vertsheet-cli.exe (command line)."""
import sys

from vertsheet.__main__ import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("--help")
    sys.exit(main())

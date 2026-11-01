"""Entry point for the packaged Cuelog.exe (also runs the player window with --player)."""
import sys

from cuelog.app import main

if __name__ == "__main__":
    sys.exit(main())

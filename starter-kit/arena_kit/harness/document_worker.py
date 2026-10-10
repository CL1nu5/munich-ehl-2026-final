"""Isolate document parsers so malformed files cannot hang the engine executor."""

import sys
from pathlib import Path

from ..docs import read_document

if __name__ == "__main__":
    sys.stdout.write(read_document(sys.argv[1], Path(sys.argv[2]).read_bytes()))

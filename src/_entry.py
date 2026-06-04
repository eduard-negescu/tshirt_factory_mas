import sys
from pathlib import Path

_src = Path(__file__).resolve().parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from main import main  # noqa: E402

if __name__ == "__main__":
    main()

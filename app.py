import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from evidence_lab.application.app.gradio_app import main  # noqa: E402

if __name__ == "__main__":
    main()

import json
from pathlib import Path

STATE_FILE = ".sync_state.json"

def load_state(folder: str) -> dict:
    path = Path(folder) / STATE_FILE

    if not path.exists():
        return {"checkpoint": 0, "files": {}}

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_state(folder: str, state: dict) -> None:
    path = Path(folder) / STATE_FILE
    temporary_path = path.with_suffix(".tmp")

    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)

    temporary_path.replace(path)

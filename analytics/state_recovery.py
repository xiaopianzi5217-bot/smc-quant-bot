import json
import os
import json
import time
import tempfile

STATE_FILE = "data/positions_state.json"

def _safe_write(path, data):

    os.makedirs(
        os.path.dirname(path),
        exist_ok=True
    )

    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(path)
    )

    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            tmp,
            path
        )

    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)

def save_positions(positions):

    payload={

        "timestamp":
            time.time(),

        "positions":
            positions

    }

    try:

        _safe_write(
            STATE_FILE,
            payload
        )

        return True

    except Exception as e:

        print(
            "[StateRecovery] save error:",
            e
        )

        return False

def load_positions():

    if not os.path.exists(
        STATE_FILE
    ):
        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data=json.load(f)

        return data.get(
            "positions",
            {}
        )

    except Exception as e:

        print(
            "[StateRecovery] load error:",
            e
        )

        return {}

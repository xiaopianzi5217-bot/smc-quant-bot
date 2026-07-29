# -*- coding: utf-8 -*-
import json
from pathlib import Path

DEFAULT_PARAM_PATH = Path('config/v6_params.json')


def load_v6_params(path=None):
    path = Path(path or DEFAULT_PARAM_PATH)
    if not path.exists():
        return {}
    with path.open('r', encoding='utf-8') as f:
        return json.load(f)

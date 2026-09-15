# -*- coding: utf-8 -*-
"""ML 权重本地持久化 + Hugging Face Dataset 同步。

与 v6_research.db 共用 HF_DATASET_REPO / HF_TOKEN。
模型路径优先 /app/data/ml_models（Space 可挂持久卷或随 Dataset 恢复）。
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional, Sequence

try:
    from utils.structured_logger import slog
except Exception:  # pragma: no cover
    import logging
    slog = logging.getLogger("smc_bot")

MODEL_DIR_CANDIDATES = (
    Path("/app/data/ml_models"),
    Path("data/ml_models"),
    Path("models"),
)

# EVRealityGuard 使用的标准文件名
EV_PROFIT = "ev_profit_model.pkl"
EV_VALUE = "ev_value_model.pkl"
EV_META = "ev_model_metadata.json"
DEFAULT_EV_FILES = (EV_PROFIT, EV_VALUE, EV_META)


def get_model_dir(create: bool = True) -> Path:
    for p in MODEL_DIR_CANDIDATES:
        try:
            if create:
                p.mkdir(parents=True, exist_ok=True)
            if p.exists() or create:
                return p
        except Exception:
            continue
    p = Path("data/ml_models")
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def resolve_model_dir(preferred: Optional[str] = None) -> str:
    """供 EVRealityGuard(model_dir=...) 使用：空/默认 models 时改走持久目录。"""
    if preferred and preferred not in ("models", "ml/models", "./models"):
        Path(preferred).mkdir(parents=True, exist_ok=True)
        return preferred
    return str(get_model_dir(True))


def _hf_config():
    repo_id = os.environ.get("HF_DATASET_REPO") or os.environ.get(
        "V6_SNAPSHOT_DATASET", "Aisvbo/svb-bot-v6-snapshots"
    )
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    return repo_id, token


def pull_models_from_hf_dataset(
    names: Optional[Sequence[str]] = None,
    model_dir: Optional[str] = None,
) -> int:
    """启动时从 Dataset 拉取 ml_models/* 到本地。"""
    repo_id, token = _hf_config()
    if not repo_id or not token:
        return 0
    try:
        from huggingface_hub import hf_hub_download
    except Exception as e:
        slog.warning(f"[MLStore] huggingface_hub 不可用: {e}")
        return 0
    dest = Path(model_dir) if model_dir else get_model_dir(True)
    dest.mkdir(parents=True, exist_ok=True)
    names = list(names) if names else list(DEFAULT_EV_FILES)
    n = 0
    for name in names:
        try:
            downloaded = hf_hub_download(
                repo_id=repo_id,
                filename=f"ml_models/{name}",
                repo_type="dataset",
                token=token,
            )
            target = dest / name
            data = Path(downloaded).read_bytes()
            target.write_bytes(data)
            n += 1
            slog.info(f"[MLStore] 云端→本地模型: {name} -> {target}")
        except Exception as e:
            err = str(e)
            if "404" in err or "Entry Not Found" in err or "not found" in err.lower():
                slog.debug(f"[MLStore] 云端无模型文件 {name}")
            else:
                slog.debug(f"[MLStore] 拉取 {name} 失败: {e}")
    return n


def push_models_to_hf_dataset(
    names: Optional[Sequence[str]] = None,
    model_dir: Optional[str] = None,
) -> int:
    """训练后把本地模型推到 Dataset ml_models/。"""
    repo_id, token = _hf_config()
    if not repo_id or not token:
        slog.debug("[MLStore] 无 HF 配置，跳过模型上传")
        return 0
    try:
        from huggingface_hub import HfApi
    except Exception as e:
        slog.warning(f"[MLStore] huggingface_hub 不可用: {e}")
        return 0
    dest = Path(model_dir) if model_dir else get_model_dir(False)
    if not dest.exists():
        return 0
    api = HfApi(token=token)
    try:
        api.create_repo(repo_id=repo_id, repo_type="dataset", private=True, exist_ok=True)
    except Exception:
        pass
    if names:
        files = [dest / n for n in names if (dest / n).exists()]
    else:
        files = list(dest.glob("*.pkl")) + list(dest.glob("*.joblib")) + list(dest.glob("*.json"))
        # 只传 EV 相关，避免误传杂文件
        files = [f for f in files if f.name.startswith("ev_") or f.name.endswith(".joblib")]
    n = 0
    for f in files:
        try:
            api.upload_file(
                path_or_fileobj=str(f),
                path_in_repo=f"ml_models/{f.name}",
                repo_id=repo_id,
                repo_type="dataset",
                token=token,
                commit_message=f"ml model backup {f.name} @ {int(time.time())}",
            )
            n += 1
            slog.info(f"[MLStore] 已上传模型: ml_models/{f.name}")
        except Exception as e:
            slog.error(f"[MLStore] 上传失败 {f.name}: {e}")
    return n

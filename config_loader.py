"""配置加载：config.yaml 为主，config.local.yaml 覆盖。

密钥、邮箱授权码之类只放 config.local.yaml —— 这个文件名已经在 .gitignore 里。
"""
import copy
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

BASE_NAME = "config.yaml"
LOCAL_NAME = "config.local.yaml"


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("读取 %s 失败: %s", path, e)
        return {}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str = BASE_NAME) -> dict:
    base_path = Path(path)
    cfg = _read(base_path)
    local = _read(base_path.with_name(LOCAL_NAME))
    if not local:
        return cfg
    merged = deep_merge(cfg, local)
    logger.info("已用 %s 覆盖 %s", LOCAL_NAME, base_path.name)
    return merged
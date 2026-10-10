"""業界マスタ(core/industries.yaml)の読み込みと、統計の産業名との照合。"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

PATH = Path(__file__).resolve().parent / "industries.yaml"


@lru_cache(maxsize=1)
def load() -> tuple[dict, ...]:
    return tuple(yaml.safe_load(PATH.read_text(encoding="utf-8")))


def by_slug() -> dict[str, dict]:
    return {i["slug"]: i for i in load()}


def norm(name: str) -> str:
    """表記ゆれを消す: 全角→半角、空白除去、読点の統一、先頭の分類記号(C や Ｃ)を外す。"""
    s = unicodedata.normalize("NFKC", name or "")
    s = re.sub(r"\s+", "", s).replace("、", ",").replace("，", ",")
    s = re.sub(r"^[A-Z][\s.:：]?(?=[^\x00-\x7f])", "", s)  # 「C鉱業…」→「鉱業…」
    return s


def match(estat_name: str) -> dict | None:
    """e-Stat 等の産業名から業界を引く。完全一致 → 前方一致の順。産業計などは None。"""
    n = norm(estat_name)
    for ind in load():
        if norm(ind["name"]) == n:
            return ind
    for ind in load():
        k = norm(ind["name"])
        if n.startswith(k) or k.startswith(n) and len(n) >= 3:
            return ind
    return None

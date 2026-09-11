# -*- coding: utf-8 -*-
"""商品编码 → 品类中文名 映射：JSON 文件持久化，可随时增补和修改。

存储位置：backend/data/product_map.json，按库隔离（避免三套体系编码冲突）：
    {
      "dashenlin": {"8106225": "易善复", ...},
      "gaoji": {},
      "haiwang": {}
    }

设计要点：
- 编码统一以「字符串」为键：库里 BIGINT 查出来是 int，使用方用 str(编码) 即可命中。
- 每次读取都重新读盘：文件只有几 KB，代价可忽略。这样无论是走接口改，
  还是运维直接手改文件，下一个请求立即生效，不需要重启服务。
- 写入用「临时文件 + os.replace」原子替换，避免写一半被读到半截 JSON。
- 文件损坏 / 缺失时回退种子数据，绝不让统计接口因此挂掉。
"""
import json
import os
import threading
from typing import Dict

from ..config import DATA_DIR

# 首次使用时的种子数据（大参林，业务提供）
_SEED: Dict[str, Dict[str, str]] = {
    "dashenlin": {
        "8106225": "易善复",
        "8116064": "老骨通",
        "1127842": "骨通（PIB10）",
        "1100881": "骨通（PIB6）",
        "1128558": "麝香7*10*10贴",
        "1104792": "康泰",
        "1086127": "天和追风",
        "2275052": "维D30粒",
        "1103648": "消痔4",
        "1091138": "益血生84粒",
        "8105101": "气滞胃痛颗粒",
        "2402446": "护肝片104片",
        "1058746": "硝呋太尔",
        "2235014": "维D32粒",
        "8108236": "锌钙特（PVC24支）",
        "1189417": "锌钙特（24袋）",
    },
    "gaoji": {},
    "haiwang": {},
}

_LOCK = threading.Lock()


def _file_path() -> str:
    return os.path.join(DATA_DIR, "product_map.json")


def _save_raw(data: Dict[str, Dict[str, str]]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = _file_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_raw() -> Dict[str, Dict[str, str]]:
    """读盘并做基本形状校验；文件不存在时用种子数据初始化。"""
    path = _file_path()
    if not os.path.isfile(path):
        data = {k: dict(v) for k, v in _SEED.items()}
        _save_raw(data)
        return data
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # 保留损坏文件便于排查，但不让统计挂掉
        return {k: dict(v) for k, v in _SEED.items()}
    if not isinstance(data, dict):
        return {k: dict(v) for k, v in _SEED.items()}

    # 规整结构：{db_key: {code(str): name(str)}}，过滤空名称
    result: Dict[str, Dict[str, str]] = {}
    for db_key, mapping in data.items():
        if isinstance(mapping, dict):
            result[str(db_key)] = {
                str(code): str(name).strip()
                for code, name in mapping.items()
                if name is not None and str(name).strip() != ""
            }
    for k in _SEED:
        result.setdefault(k, {})
    return result


def get_product_map(db_key: str) -> Dict[str, str]:
    """取某个库的映射（编码 -> 中文名）。只读，不加锁。"""
    return _load_raw().get(db_key, {})


def replace_product_map(db_key: str, mapping: Dict[str, str]) -> Dict[str, str]:
    """整体替换某个库的映射（管理接口「保存」时用）。返回清洗后的结果。"""
    cleaned = {
        str(c).strip(): str(n).strip()
        for c, n in mapping.items()
        if str(c).strip() and str(n).strip()
    }
    with _LOCK:
        data = _load_raw()
        data[db_key] = cleaned
        _save_raw(data)
    return cleaned


def delete_product_code(db_key: str, code: str) -> bool:
    """删除单条映射，返回是否确实删除了。"""
    with _LOCK:
        data = _load_raw()
        mapping = data.get(db_key, {})
        key = str(code)
        if key not in mapping:
            return False
        del mapping[key]
        data[db_key] = mapping
        _save_raw(data)
        return True

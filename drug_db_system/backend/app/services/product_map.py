# -*- coding: utf-8 -*-
"""商品编码 → 品类中文名 / 开票价 映射：JSON 文件持久化，可随时增补和修改。

存储位置：backend/data/product_map.json，按库隔离（避免三套体系编码冲突）：
    {
      "dashenlin": {"8106225": {"name": "易善复", "price": 61.7}, ...},
      "gaoji": {},
      "haiwang": {}
    }

兼容：旧格式 {code: "名称"} 读取时自动升级为 {name, price: null}，下次保存即落新格式。

设计要点：
- 编码统一以「字符串」为键：库里 BIGINT 查出来是 int，使用方用 str(编码) 即可命中。
- 每次读取都重新读盘：文件只有几 KB，代价可忽略。这样无论是走接口改，
  还是运维直接手改文件，下一个请求立即生效，不需要重启服务。
- 写入用「临时文件 + os.replace」原子替换，避免写一半被读到半截 JSON。
- 文件损坏 / 缺失 / 为空时回退种子数据，绝不让统计接口因此挂掉。

开票价用途：「实销金额」= SUM(数量 × 开票价)；未配置价格的品类不计入金额口径。
"""
import json
import os
import threading
from typing import Dict, Optional, Union

from ..config import DATA_DIR

# 单条映射：name=品类中文名（必填），price=开票价/元（可空，用于实销金额）
MapValue = Dict[str, Optional[float]]

# 首次使用时的种子数据（大参林，业务提供）
_SEED: Dict[str, Dict[str, MapValue]] = {
    "dashenlin": {
        "8106225": {"name": "易善复", "price": 61.7},
        "8116064": {"name": "老骨通", "price": 18.5},
        "1127842": {"name": "骨通（PIB10）", "price": 23},
        "1100881": {"name": "骨通（PIB6）", "price": 12},
        "1128558": {"name": "麝香7*10*10贴", "price": 7.8},
        "1104792": {"name": "康泰", "price": 8.5},
        "1086127": {"name": "天和追风", "price": 11.86},
        "2275052": {"name": "维D30粒", "price": 8.77},
        "1103648": {"name": "消痔4", "price": 29.8},
        "1091138": {"name": "益血生84粒", "price": 67.5},
        "8105101": {"name": "气滞胃痛颗粒", "price": 18.5},
        "2402446": {"name": "护肝片104片", "price": 8.2},
        "1058746": {"name": "硝呋太尔", "price": 18.7},
        "2235014": {"name": "维D32粒", "price": 8.77},
        "8108236": {"name": "锌钙特（PVC24支）", "price": 10},
        "1189417": {"name": "锌钙特（24袋）", "price": 13},
    },
    "gaoji": {},
    "haiwang": {},
}

_LOCK = threading.Lock()


def _file_path() -> str:
    return os.path.join(DATA_DIR, "product_map.json")


def _norm_value(value: Union[str, dict]) -> Optional[MapValue]:
    """把 str / dict 两种写法规整成 {name, price}；name 为空视为无效。"""
    if isinstance(value, str):
        name, price = value.strip(), None
    elif isinstance(value, dict):
        name = str(value.get("name", "")).strip()
        raw = value.get("price")
        try:
            price = float(raw) if raw is not None and str(raw).strip() != "" else None
        except (TypeError, ValueError):
            price = None
    else:
        return None
    if not name:
        return None
    return {"name": name, "price": price}


def _clean(mapping: dict) -> Dict[str, MapValue]:
    """规整一个库的映射：编码转字符串、剔除无效行。"""
    result: Dict[str, MapValue] = {}
    for code, value in (mapping or {}).items():
        key = str(code).strip()
        if not key:
            continue
        norm = _norm_value(value)
        if norm:
            result[key] = norm
    return result


def _save_raw(data: Dict[str, Dict[str, MapValue]]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = _file_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_raw() -> Dict[str, Dict[str, MapValue]]:
    """读盘并做基本形状校验（含旧格式升级）；文件不存在时用种子数据初始化。"""
    path = _file_path()
    if not os.path.isfile(path):
        data = {k: _clean(v) for k, v in _SEED.items()}
        _save_raw(data)
        return data
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return {k: _clean(v) for k, v in _SEED.items()}
    if not raw.strip():
        # 空文件（如被误清空）：按「文件缺失」处理并写回种子数据，
        # 避免长期静默回退、让人以为自己维护的映射丢了
        data = {k: _clean(v) for k, v in _SEED.items()}
        _save_raw(data)
        return data
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 保留损坏文件便于排查，但不让统计挂掉
        return {k: _clean(v) for k, v in _SEED.items()}
    if not isinstance(data, dict):
        return {k: _clean(v) for k, v in _SEED.items()}

    # 规整结构：{db_key: {code(str): {name, price}}}，剔除无效行
    result: Dict[str, Dict[str, MapValue]] = {}
    for db_key, mapping in data.items():
        if isinstance(mapping, dict):
            result[str(db_key)] = _clean(mapping)
    for k in _SEED:
        result.setdefault(k, {})
    return result


def get_product_map_full(db_key: str) -> Dict[str, MapValue]:
    """取某个库的完整映射（编码 -> {name, price}）。只读，不加锁。"""
    return _load_raw().get(db_key, {})


def get_product_map(db_key: str) -> Dict[str, str]:
    """取某个库的编码 → 中文名映射（兼容旧调用方）。"""
    return {code: v["name"] for code, v in get_product_map_full(db_key).items()}


def replace_product_map(
    db_key: str, mapping: Dict[str, Union[str, dict]]
) -> Dict[str, MapValue]:
    """整体替换某个库的映射（管理接口「保存」时用）。返回清洗后的结果。"""
    cleaned = _clean(mapping)
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

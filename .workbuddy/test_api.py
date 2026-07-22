# -*- coding: utf-8 -*-
import urllib.request
import urllib.parse
import json

BASE = "http://127.0.0.1:8000"

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())

def get(path, params=None):
    if params:
        q = urllib.parse.urlencode(params)
        path = f"{path}?{q}"
    return call("GET", path)

lines = []

def log(s):
    lines.append(s)

# 1) 列出库
dbs = get("/api/dbs")
log("=== 1) 三个数据库 ===")
for d in dbs:
    log(f"  {d['label']}({d['db_name']}) 行数={d['row_count']} 字段={len(d['columns'])}")

# 1.5) 清理上次可能残留的测试记录，保证幂等
for r in get("/api/dashenlin/rows", {"page": 1, "page_size": 100, "search": "测试"})["rows"]:
    call("DELETE", f"/api/dashenlin/rows/{r['id']}")
log("\n(已清理残留测试记录)")

# 2) 高济首条完整数据
rows = get("/api/gaoji/rows", {"page": 1, "page_size": 1})
log("\n=== 2) 高济首条完整数据 ===")
for k, v in rows["rows"][0].items():
    log(f"  {k} = {v}")

# 3) 新增大参林一条记录
new = call("POST", "/api/dashenlin/rows", {
    "公司": "测试公司", "门店名称": "测试门店",
    "商品名称": "测试药品", "数量": 10, "销售价格": 25.5})
log("\n=== 3) 新增记录 ===")
log(f"  {new}")
new_id = new["id"]

# 4) 编辑该记录
upd = call("PUT", f"/api/dashenlin/rows/{new_id}", {"商品名称": "测试药品(改)", "数量": 99})
log("\n=== 4) 编辑记录 ===")
log(f"  {upd}")

# 5) 查回验证
chk = get("/api/dashenlin/rows", {"page": 1, "page_size": 100, "search": "测试"})
log("\n=== 5) 搜索'测试'验证 ===")
for r in chk["rows"]:
    log(f"  id={r['id']} 商品={r.get('商品名称')} 数量={r.get('数量')} 价格={r.get('销售价格')}")

# 6) 排序测试：大参林按数量降序
sor = get("/api/dashenlin/rows", {"page": 1, "page_size": 10, "sort_by": "数量", "sort_dir": "desc"})
log("\n=== 6) 大参林按数量降序 ===")
for r in sor["rows"]:
    log(f"  id={r['id']} 商品={r.get('商品名称')} 数量={r.get('数量')}")

# 7) 删除记录
dele = call("DELETE", f"/api/dashenlin/rows/{new_id}")
log("\n=== 7) 删除记录 ===")
log(f"  {dele}")

# 8) 删除后确认行数
dbs2 = get("/api/dbs")
log("\n=== 8) 删除后行数 ===")
for d in dbs2:
    log(f"  {d['label']}: {d['row_count']}")

out = "\n".join(lines)
with open(r"D:\ASUS\CRDB\.workbuddy\api_test_result.txt", "w", encoding="utf-8") as f:
    f.write(out)
print(out)

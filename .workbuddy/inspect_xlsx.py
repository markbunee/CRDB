import pandas as pd
import os
import json

base = r"D:\ASUS\CRDB"
files = ["大参林医药集团数据.xlsx", "高济.xlsx", "海王数据.xlsx"]
out = {}

for f in files:
    path = os.path.join(base, f)
    info = {"file": f, "sheets": {}}
    xls = pd.ExcelFile(path)
    for sheet in xls.sheet_names:
        full = pd.read_excel(path, sheet_name=sheet)
        cols = []
        for c in full.columns:
            col_data = full[c]
            sample = col_data.dropna().head(3).tolist()
            sample = [str(s) for s in sample]
            cols.append({
                "name": str(c),
                "dtype": str(col_data.dtype),
                "non_null": int(col_data.notna().sum()),
                "total": int(len(col_data)),
                "sample": sample
            })
        info["sheets"][sheet] = {
            "rows": int(full.shape[0]),
            "cols": int(full.shape[1]),
            "columns": cols
        }
    out[f] = info

outpath = os.path.join(base, ".workbuddy", "headers.json")
with open(outpath, "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2)
print("WROTE", outpath)

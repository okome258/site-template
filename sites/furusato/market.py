"""市販価格(e-Stat 小売物価統計調査)を取ってくる。

まだ表の形を確認中なので、見つかった表と中身の見本を data/market_debug.json に書き出す。
失敗してもサイトのビルドは止めない。
"""

from __future__ import annotations

import json
from pathlib import Path

from core.estat import EStat, EStatError

STATS_CODE = "00200571"  # 小売物価統計調査
TABLE_ID = "0003421913"  # 主要品目の都市別小売価格
AREA = "13100"  # 東京都区部
SEARCH_WORDS = ["主要品目の東京都区部小売価格", "主要品目の都市別小売価格", "小売価格"]


def discover(out_dir: Path) -> dict:
    api = EStat()
    if not api.ready:
        return {"status": "no_key"}
    dbg: dict = {"tables": [], "samples": []}
    try:
        seen = {}
        for w in SEARCH_WORDS:
            for t in api.list_tables(STATS_CODE, w, limit=200):
                seen[t["id"]] = t
        tables = sorted(seen.values(), key=lambda t: (t["survey_date"], t["id"]), reverse=True)
        dbg["tables"] = [{k: t[k] for k in ("id", "title", "survey_date", "open_date", "statistics_name")}
                         for t in tables[:80]]
        # 「主要品目」の新しい表をいくつか中身を見る
        picks = [t for t in tables if "主要品目" in t["title"] or "主要品目" in t["statistics_name"]][:4]
        for t in picks:
            meta = api.get_data(t["id"], limit=20)
            dbg["samples"].append({
                "id": t["id"], "title": t["title"], "statistics_name": t["statistics_name"],
                "classes": {cid: {"name": c["name"], "n": len(c["order"]),
                                  "items": [[x, c["items"][x]] for x in c["order"][:400]]}
                            for cid, c in meta["classes"].items()},
                "values": meta["values"][:20]})
        codes = ["01001","01201","01211","01221","01133","01114","01167","01106","01511","01502","01341",
                 "01801","01142","01101","01132","01261","01533","01571","01551","01563","01982","02021",
                 "02003","01953","01031","01844","01881"]
        vals = api.get_data(TABLE_ID, cdCat02=",".join(codes), cdArea=AREA, cdTimeFrom="2025000901")
        names = vals["classes"].get("cat02", {}).get("items", {})
        rows = {}
        for v in vals["values"]:
            rows.setdefault(names.get(v["cat02"], v["cat02"]), []).append([v["time"], v["value"], v.get("unit")])
        dbg["prices"] = {k: sorted(r, reverse=True)[:13] for k, r in rows.items()}
        dbg["status"] = "ok"
    except EStatError as e:
        dbg["status"] = f"error: {e}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "market_debug.json").write_text(json.dumps(dbg, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[market] {dbg['status']} 表{len(dbg['tables'])}件 見本{len(dbg['samples'])}件")
    return dbg

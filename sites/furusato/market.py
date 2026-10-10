"""市販価格(総務省 小売物価統計調査・東京都区部)を e-Stat から取ってくる。

各カテゴリの「1単位(サイトの表示単位)あたりの市販価格」を出し、
返礼品の内容量 × 市販価格 で「スーパーで買うと何円分か」の目安を計算する。

銘柄の単位は、2026年10月に実際の価格の大きさで確かめたものだけ載せている。
単位が確かめられない銘柄(うなぎ・ソーセージ・清酒・メロン等)は入れない。
"""

from __future__ import annotations

import json
from pathlib import Path

from core.estat import CREDIT, EStat, EStatError

TABLE_ID = "0003421913"  # 小売物価統計調査(動向編) 主要品目の都市別小売価格
AREA = "13100"  # 東京都区部
AREA_NAME = "東京都区部"
TIME_FROM_MONTHS = 13  # この月数さかのぼって、値のある最新の月を使う(旬の果物は冬に値が無い)

# カテゴリID: (銘柄コード, 調査の単位をサイトの表示単位にした量, 調査の単位の書き方)
ITEMS = {
    "rice": ("01001", 5, "5kg"),
    "beef": ("01201", 0.1, "100g"),
    "pork": ("01211", 0.1, "100g"),
    "chicken": ("01221", 0.1, "100g"),
    "tuna": ("01101", 0.1, "100g"),
    "salmon": ("01106", 0.1, "100g"),
    "shrimp": ("01114", 0.1, "100g"),
    "scallop": ("01133", 0.1, "100g"),
    "oyster": ("01132", 0.1, "100g"),
    "ikura": ("01167", 100, "100g"),  # いくらはサイトの単位が g
    "egg": ("01341", 10, "10個"),
    "apple": ("01502", 1, "1kg"),
    "mikan": ("01511", 1, "1kg"),
    "shine": ("01533", 1, "1kg"),
    "peach": ("01551", 1, "1kg"),
    "strawberry": ("01571", 1, "1kg"),
    "water": ("01982", 2, "2L"),
    "beer": ("02021", 2.1, "350ml×6缶"),
}


def _ym(code: str) -> str:
    # e-Stat の時間コード 2026000808 → 2026年8月
    return f"{int(code[:4])}年{int(code[6:8])}月" if len(code) >= 8 else code


def fetch(out_dir: Path, today: str) -> dict:
    """市販価格を取得して data/market.json に保存。失敗したら前回の保存分を返す。"""
    path = out_dir / "market.json"
    prev = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    api = EStat()
    if not api.ready:
        return prev
    y, m = int(today[:4]), int(today[5:7])
    m0 = (y * 12 + m - 1) - TIME_FROM_MONTHS
    since = f"{m0 // 12}00{m0 % 12 + 1:02d}{m0 % 12 + 1:02d}"
    try:
        codes = {v[0]: k for k, v in ITEMS.items()}
        res = api.get_data(TABLE_ID, cdCat02=",".join(codes), cdArea=AREA, cdTimeFrom=since)
    except EStatError as e:
        print(f"::warning::市販価格の取得に失敗、前回の値を使う: {e}")
        return prev
    names = res["classes"].get("cat02", {}).get("items", {})
    latest: dict[str, tuple[str, float]] = {}
    for v in res["values"]:
        cid = codes.get(v.get("cat02"))
        if cid and v["value"] and (cid not in latest or v["time"] > latest[cid][0]):
            latest[cid] = (v["time"], v["value"])
    items = {}
    for cid, (t, val) in latest.items():
        code, amount, label = ITEMS[cid]
        name = names.get(code, "").split(" ", 1)[-1].split("【")[0]
        items[cid] = {"name": name, "yen": val, "per": label, "ym": _ym(t),
                      "per_unit": val / amount}
    if not items:
        return prev
    data = {"area": AREA_NAME, "table": TABLE_ID, "credit": CREDIT, "fetched": today, "items": items}
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[market] {len(items)}カテゴリの市販価格を取得")
    return data


def apply(categories: dict, market: dict) -> None:
    """返礼品ごとに「市販だと何円分か」と「寄付額の何%分か」をつける。"""
    for cid, mk in (market.get("items") or {}).items():
        for it in categories.get(cid, {}).get("items", []):
            yen = it["qty"] * mk["per_unit"]
            it["market_yen"] = round(yen, -1)
            it["market_pct"] = round(yen / it["price"] * 100) if it["price"] else None

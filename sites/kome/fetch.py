"""お米の最安値ウォッチ: 楽天市場のお米を毎日取得し、価格履歴(data/history/YYYY-MM.csv)に追記する。

いまは「記録だけ」の段階。履歴が1〜2週間たまったら、
過去最安値との差・補正レビュースコア・送料込み単価でページを作る。
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.qty import parse_quantity
from core.rakuten import Rakuten, RakutenError

JST = timezone(timedelta(hours=9))
HISTORY_FIELDS = ["date", "code", "kind", "price", "qty_kg", "postage_included", "point_rate",
                  "review_count", "review_avg", "available", "shop"]


def kind_of(cfg: dict, name: str) -> str:
    """白米(hakumai)・無洗米(musen)・玄米(genmai)・もち米(mochi)。単価はこの中でだけ比べる。"""
    for kind, words in cfg.get("kinds", []):
        if any(w in name for w in words):
            return kind
    return "hakumai"


def normalize(cfg: dict, raw: list[dict]) -> tuple[list[dict], dict]:
    """APIの商品を、比較できる形に揃える。読めない・米でないものは捨てる。"""
    lo, hi = cfg["qty_range"]
    seen, rows, dropped = set(), [], {}

    def drop(reason):
        dropped[reason] = dropped.get(reason, 0) + 1

    for it in raw:
        code = it.get("itemCode")
        if not code or code in seen:
            continue
        seen.add(code)
        name, price = it.get("itemName", ""), it.get("itemPrice") or 0
        if any(w in name for w in cfg["exclude"]):
            drop("対象外"); continue
        if not any(w in name for w in cfg["require"]):
            drop("米以外"); continue
        if price <= 0:
            continue
        q, evidence = parse_quantity(name, "weight")
        if q is None:
            drop(evidence); continue
        if not (lo <= q <= hi):
            drop("量が範囲外"); continue
        rows.append({
            "code": code,
            "kind": kind_of(cfg, name),
            "name": name.strip(),
            "price": price,
            "qty_kg": round(q, 3),
            "evidence": evidence,
            "per_kg": round(price / q),
            "postage_included": it.get("postageFlag") == 0,  # 0=送料込み
            "point_rate": it.get("pointRate") or 1,
            "review_count": it.get("reviewCount") or 0,
            "review_avg": it.get("reviewAverage") or 0,
            "available": it.get("availability", 1) == 1,
            "shop": it.get("shopName", ""),
            "url": it.get("affiliateUrl") or it.get("itemUrl", ""),
        })
    rows.sort(key=lambda r: r["per_kg"])
    return rows, dropped


def append_history(hist_dir: Path, rows: list[dict], today: str) -> Path:
    """その日の記録を月別CSVに書く。同じ日に2回動いたら、その日の分は後の結果で置き換える。"""
    hist_dir.mkdir(parents=True, exist_ok=True)
    path = hist_dir / f"{today[:7]}.csv"
    kept = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as f:
            kept = [r for r in csv.DictReader(f) if r["date"] != today]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        w.writeheader()
        w.writerows(kept)
        for r in rows:
            w.writerow({"date": today, **{k: r[k] for k in HISTORY_FIELDS if k != "date"}})
    return path


def fetch(cfg: dict) -> dict:
    f = cfg["fetch"]
    api = Rakuten(referer=cfg["site"]["rakuten_referer"])
    if not api.ready:
        raise RakutenError("RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY が未設定です")
    raw, errors = [], []
    for kw in f["keywords"]:
        for sort in f["sorts"]:
            for page in range(1, f["pages"] + 1):
                params = {"hits": f["hits"], "page": page, "sort": sort,
                          "minPrice": f["min_price"], "maxPrice": f["max_price"]}
                if f.get("ng_keyword"):
                    params["NGKeyword"] = f["ng_keyword"]
                try:
                    raw += api.search_items(kw, **params)
                except RakutenError as e:
                    errors.append(f"{kw}/{sort}: {e}")
                    print("::warning::", errors[-1])
                    break
    rows, dropped = normalize(cfg, raw)
    print(f"[kome] 候補{len(raw)}件 → 記録{len(rows)}件 除外{dropped}")
    if not rows:
        raise RakutenError("0件でした: " + " / ".join(errors[:3]))  # 前回データで生成させる
    today = datetime.now(JST).strftime("%Y-%m-%d")
    path = append_history(cfg["_dir"] / "data" / "history", rows, today)
    print(f"[kome] 価格履歴を記録 → {path.name}")
    return {"items": rows, "dropped": dropped, "errors": errors[:10]}


def history_days(cfg: dict) -> int:
    d = cfg["_dir"] / "data" / "history"
    days = set()
    for p in sorted(d.glob("*.csv")) if d.exists() else []:
        with p.open(encoding="utf-8", newline="") as f:
            days |= {r["date"] for r in csv.DictReader(f)}
    return len(days)


def pages(cfg: dict, data: dict) -> list[dict]:
    # 準備中なので検索には出さない(sitemap にも載らない)
    return [{"path": "index.html", "template": "index.html", "noindex": True,
             "context": {"days": history_days(cfg)}}]

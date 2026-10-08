"""ふるさと納税 量コスパ比較: 楽天市場から返礼品を集め、寄付1万円あたりの量でランキングする。"""

from __future__ import annotations

import re

from core.qty import parse_quantity
from core.rakuten import Rakuten, RakutenError

FURUSATO_SHOP = re.compile(r"^f\d{6}")  # 楽天ふるさと納税の自治体ショップ(f + 自治体コード)


def _image(it: dict) -> str:
    urls = it.get("mediumImageUrls") or []
    u = urls[0] if urls else ""
    if isinstance(u, dict):  # formatVersion=1 の形でも動くように
        u = u.get("imageUrl", "")
    return re.sub(r"\?_ex=\d+x\d+", "?_ex=240x240", u)


def _is_furusato(it: dict) -> bool:
    return bool(FURUSATO_SHOP.match(it.get("shopCode", ""))) or "ふるさと納税" in it.get("itemName", "")


def rank_category(cat: dict, raw: list[dict], top_n: int) -> dict:
    lo, hi = cat["range"]
    seen, rows, dropped = set(), [], {}

    def drop(reason):
        dropped[reason] = dropped.get(reason, 0) + 1

    for it in raw:
        code = it.get("itemCode")
        if not code or code in seen:
            continue
        seen.add(code)
        name, price = it.get("itemName", ""), it.get("itemPrice") or 0
        if not _is_furusato(it):
            drop("ふるさと納税以外"); continue
        if it.get("availability", 1) != 1:
            drop("在庫なし"); continue
        if any(w in name for w in cat.get("exclude", [])):
            drop("対象外の商品"); continue
        if cat.get("require") and not any(w in name for w in cat["require"]):
            drop("対象外の商品"); continue  # 商品名にカテゴリの言葉が無いもの(別の品)
        if price <= 0:
            continue
        q, evidence = parse_quantity(name, cat["unit_kind"], cat.get("count_units"))
        if q is None:
            drop(evidence); continue
        q *= cat.get("scale", 1)  # 表示単位へ(いくら: kg → g)
        if not (lo <= q <= hi):
            drop("量が範囲外"); continue
        rows.append({
            "code": code,
            "name": re.sub(r"^【ふるさと納税】\s*", "", name).strip(),
            "price": price,
            "qty": round(q, 3),
            "evidence": evidence,
            "per10k": round(q / price * 10000, 3),
            "unit_price": round(price / q),  # 1単位あたりの寄付額
            "shop": it.get("shopName", ""),
            "url": it.get("affiliateUrl") or it.get("itemUrl", ""),
            "portal": "楽天ふるさと納税",
            "image": _image(it),
            "review_avg": it.get("reviewAverage") or 0,
            "review_count": it.get("reviewCount") or 0,
            "wakeari": "訳あり" in name or "訳アリ" in name,
        })
    rows.sort(key=lambda r: (-r["per10k"], -r["review_count"]))
    # 相場(比較対象すべての中央値)。各商品が相場の何倍の量かを出して「比較サイト」として読めるように
    summary = {}
    if rows:
        per = sorted(r["per10k"] for r in rows)
        up = sorted(r["unit_price"] for r in rows)
        med = per[len(per) // 2]
        summary = {"median_per10k": med, "median_unit_price": up[len(up) // 2],
                   "min_unit_price": up[0], "max_unit_price": up[-1],
                   "towns": len({r["shop"] for r in rows})}
        for r in rows:
            r["vs_median"] = round(r["per10k"] / med, 2) if med else None
    return {"items": rows[:top_n], "candidates": len(seen), "ranked": len(rows), "dropped": dropped,
            "summary": summary}


def fetch(cfg: dict) -> dict:
    f = cfg["fetch"]
    api = Rakuten(referer=cfg["site"]["rakuten_referer"])
    if not api.ready:
        raise RakutenError("RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY が未設定です")
    out, errors = {}, []
    for cat in cfg["categories"]:
        raw = []
        for kw in cat["keywords"]:
            for sort in f["sorts"]:
                for page in range(1, f["pages"] + 1):
                    params = {"hits": f["hits"], "page": page, "sort": sort,
                              "minPrice": f["min_price"], "maxPrice": f["max_price"],
                              "availability": 1}
                    if f.get("ng_keyword"):
                        params["NGKeyword"] = f["ng_keyword"]
                    try:
                        raw += api.search_items(kw, **params)
                    except RakutenError as e:
                        errors.append(f"{cat['id']} / {kw}: {e}")
                        print("::warning::", errors[-1])
                        break
        out[cat["id"]] = rank_category(cat, raw, f["top_n"])
        r = out[cat["id"]]
        print(f"[{cat['id']}] 候補{r['candidates']}件 → ランキング{r['ranked']}件 除外{r['dropped']}")
    if all(not v["items"] for v in out.values()):
        # 全カテゴリ空=取得失敗とみなし、前回データで生成させる
        raise RakutenError("全カテゴリで0件でした: " + " / ".join(errors[:3]))
    return {"categories": out, "errors": errors[:10]}


def pages(cfg: dict, data: dict) -> list[dict]:
    cats = cfg["categories"]
    ps = [{"path": "index.html", "template": "index.html", "context": {"cats": cats}}]
    for c in cats:
        r = data["categories"].get(c["id"], {"items": []})
        ps.append({
            "path": f"c/{c['id']}/index.html",
            "template": "category.html",
            "title": f"{c['name']}の量コスパランキング",
            "description": f"楽天ふるさと納税の{c['name']}を寄付1万円あたりの量({c['unit']})で比較。毎日自動更新。",
            "context": {"cats": cats, "cat": c, "rank": r},
        })
    ps.append({"path": "about/index.html", "template": "about.html", "title": "このサイトについて・計算方法",
               "context": {"cats": cats}, "changefreq": "monthly"})
    return ps

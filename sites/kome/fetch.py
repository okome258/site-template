"""お米の最安値ウォッチ: 楽天市場のお米を毎日取得し、価格履歴(data/history/YYYY-MM.csv)に追記して、
種類(白米・無洗米・玄米・もち米)× 量(5kg前後・10kg前後・20kg以上)ごとに 1kgあたりの値段でページを作る。
"""

from __future__ import annotations

import csv
import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.qty import parse_quantity
from core.rakuten import Rakuten, RakutenError

JST = timezone(timedelta(hours=9))
HISTORY_FIELDS = ["date", "code", "kind", "price", "qty_kg", "postage_included", "point_rate",
                  "review_count", "review_avg", "available", "shop"]


def kind_of(name: str) -> tuple[str, str]:
    """(種類, 補足)。白米(hakumai)・無洗米(musen)・玄米(genmai)・もち米(mochi)。単価はこの中でだけ比べる。

    楽天の商品名は「玄米 白米 精米」のように選べる種類を全部並べることが多い。
    玄米と白米が両方書いてあるものは、玄米の量・値段で売っている「精米も選べる玄米」として玄米に入れる。
    無洗米と白米が両方書いてあるものは、白米に入れて「無洗米も選べる」と添える。
    """
    has = lambda *ws: any(w in name for w in ws)
    if has("もち米", "餅米", "糯米"):
        return "mochi", ""
    if has("玄米"):
        return "genmai", ("精米も選べる" if has("白米", "精米", "無洗米", "分づき") else "")
    if has("無洗米"):
        return ("hakumai", "無洗米も選べる") if has("白米") else ("musen", "")
    return "hakumai", ""


def _image(it: dict) -> str:
    urls = it.get("mediumImageUrls") or []
    u = urls[0] if urls else ""
    if isinstance(u, dict):
        u = u.get("imageUrl", "")
    return re.sub(r"\?_ex=\d+x\d+", "?_ex=240x240", u)


_Z2H = str.maketrans("０１２３４５６７８９", "0123456789")


def crop_label(name: str) -> str:
    """商品名から産年を拾う(令和8年産 / R8 / 8年産)。2年分あれば「令和7・8年産」。"""
    n = name.translate(_Z2H)
    ys = sorted({int(y) for y in re.findall(r"(?:令和|R)\s*(\d{1,2})\s*年?", n) if 1 <= int(y) <= 20})
    if not ys:
        return ""
    return "令和" + "・".join(map(str, ys)) + "年産"


def tidy_name(name: str) -> str:
    """表示用に、【】や［］の宣伝文句と「※」以降の注意書きを落とす。元の名前はリンク先で見られる。"""
    t = re.split(r"[※《]", name)[0]
    t = re.sub(r"【[^】]*】|［[^］]*］|\[[^\]]*\]|＜[^＞]*＞|<[^>]*>", " ", t)
    t = re.sub(r"[♪★☆◆◇■□●○]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or name.strip()


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
        kind, option = kind_of(name)
        q, evidence = parse_quantity(name, "weight")
        if q is None:
            drop(evidence); continue
        if not (lo <= q <= hi):
            drop("量が範囲外"); continue
        rows.append({
            "code": code,
            "kind": kind,
            "option": option,
            "name": name.strip(),
            "short": tidy_name(name),
            "brand": next((b for b in cfg["brands"] if b in name), ""),
            "blend": any(w in name for w in ("ブレンド", "複数原料")),
            "crop": crop_label(name),
            "image": _image(it),
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


# ---- ページ ----

def load_history(cfg: dict) -> list[dict]:
    d = cfg["_dir"] / "data" / "history"
    rows = []
    for p in sorted(d.glob("*.csv")) if d.exists() else []:
        with p.open(encoding="utf-8", newline="") as f:
            rows += list(csv.DictReader(f))
    return rows


def band_of(cfg: dict, qty: float) -> str | None:
    for b in cfg["bands"]:
        if b["min"] <= qty <= b["max"]:
            return b["id"]
    return None


def _median(v: list[float]) -> int | None:
    return round(statistics.median(v)) if v else None


def market_history(cfg: dict, hist: list[dict]) -> dict:
    """種類 → 量の帯 → [(日付, 1kgあたり中央値)]。送料込みの商品だけで出す。"""
    acc = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for r in hist:
        if r["postage_included"] != "True":
            continue
        q = float(r["qty_kg"])
        b = band_of(cfg, q)
        if b:
            acc[r["kind"]][b][r["date"]].append(int(r["price"]) / q)
    return {k: {b: sorted((d, _median(v)) for d, v in days.items()) for b, days in bands.items()}
            for k, bands in acc.items()}


def chart(cfg: dict, series: dict, w: int = 640, h: int = 200) -> dict | None:
    """相場の推移グラフ用の座標(SVGはテンプレートで描く)。2日分以上たまってから出す。"""
    dates = sorted({d for s in series.values() for d, _ in s})
    if len(dates) < 2:
        return None
    vals = [v for s in series.values() for _, v in s]
    lo, hi = min(vals), max(vals)
    pad = max(10, (hi - lo) * 0.15)
    lo, hi = lo - pad, hi + pad
    L, R, T, B = 48, 12, 12, 26
    x = lambda d: L + (w - L - R) * dates.index(d) / (len(dates) - 1)
    y = lambda v: T + (h - T - B) * (1 - (v - lo) / (hi - lo))
    lines = []
    for b in cfg["bands"]:
        s = series.get(b["id"])
        if s:
            lines.append({"label": b["label"], "points": " ".join(f"{x(d):.1f},{y(v):.1f}" for d, v in s),
                          "last": {"x": x(s[-1][0]), "y": y(s[-1][1]), "v": s[-1][1]}})
    ticks = [{"y": y(v), "v": round(v)} for v in (lo + pad, (lo + hi) / 2, hi - pad)]
    xl = [{"x": x(d), "t": d[5:].replace("-", "/")} for d in (dates[0], dates[-1])]
    return {"w": w, "h": h, "lines": lines, "ticks": ticks, "xl": xl, "L": L, "R": R}


def item_history(hist: list[dict], today: str) -> dict:
    """商品コード → {記録日数, 記録中の最安(1kg), 前回の1kg}"""
    by = defaultdict(dict)
    for r in hist:
        by[r["code"]][r["date"]] = int(r["price"]) / float(r["qty_kg"])
    out = {}
    for code, days in by.items():
        prev = [v for d, v in sorted(days.items()) if d < today]
        out[code] = {"days": len(days), "min": round(min(days.values())),
                     "prev": round(prev[-1]) if prev else None}
    return out


def _fill(cfg: dict, it: dict) -> dict:
    """古い形式の latest.json(項目が足りない)でも生成できるように補う。"""
    name = it.get("name", "")
    kind, option = kind_of(name)
    return {"image": "", "short": tidy_name(name), "crop": crop_label(name), "option": option,
            "brand": next((b for b in cfg["brands"] if b in name), ""),
            "blend": any(w in name for w in ("ブレンド", "複数原料")), **it, "kind": kind}


def pages(cfg: dict, data: dict) -> list[dict]:
    items = [_fill(cfg, it) for it in data.get("items", [])]
    hist = load_history(cfg)
    today = (data.get("fetched_at") or datetime.now(JST).isoformat())[:10]
    days = sorted({r["date"] for r in hist})
    ih = item_history(hist, today)
    mh = market_history(cfg, hist)
    kinds = cfg["kinds_meta"]
    out = []
    for k in kinds:
        mine = [dict(it, hist=ih.get(it["code"], {})) for it in items if it["kind"] == k["id"]]
        ranked = [it for it in mine if it["postage_included"]]
        bands = []
        for b in cfg["bands"]:
            rows = sorted((it for it in ranked if band_of(cfg, it["qty_kg"]) == b["id"]),
                          key=lambda it: (it["per_kg"], -it["review_count"]))
            if rows:
                bands.append({**b, "items": rows[:cfg["top_n"]], "count": len(rows),
                              "median": _median([it["per_kg"] for it in rows])})
        out.append({
            "path": k["path"], "template": "kind.html",
            "title": k["title"], "description": k["description"],
            "context": {"kind": k, "kinds": kinds, "bands": bands, "total": len(mine),
                        "postage_extra": len(mine) - len(ranked), "days": days,
                        "chart": chart(cfg, mh.get(k["id"], {}))},
        })
    out.append({"path": "about/index.html", "template": "about.html", "title": "このサイトについて",
                "description": "お米の最安値ウォッチの集め方・比べ方・注意点。",
                "context": {"kinds": kinds, "days": days}, "changefreq": "monthly"})
    return out

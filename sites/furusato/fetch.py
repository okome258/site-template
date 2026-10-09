"""ふるさと納税 量コスパ比較: 楽天市場から返礼品を集め、寄付1万円あたりの量でランキングする。"""

from __future__ import annotations

import re

from core.qty import parse_quantity
from core.rakuten import Rakuten, RakutenError

FURUSATO_SHOP = re.compile(r"^f\d{6}")
PREF = re.compile(r"^(北海道|東京都|京都府|大阪府|.{2,3}県)")


def _town(shop: str) -> str:
    """ショップ名「福井県小浜市」→「小浜市」(他サイト検索用)。形が違えばそのまま。"""
    s = re.sub(r"[\(（].*$", "", shop).strip()
    s2 = PREF.sub("", s)
    return s2 if re.search(r"[市町村区]$", s2) else s  # 楽天ふるさと納税の自治体ショップ(f + 自治体コード)


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
            "town": _town(it.get("shopName", "")),
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
    # 人気度 = 比較対象のレビュー件数の合計(カテゴリの並び順に使う)
    popularity = sum(r["review_count"] for r in rows)
    return {"items": rows[:top_n], "candidates": len(seen), "ranked": len(rows), "dropped": dropped,
            "summary": summary, "popularity": popularity}


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


def _ym(data: dict) -> str:
    s = data.get("fetched_at", "")
    return f"{int(s[:4])}年{int(s[5:7])}月最新" if len(s) >= 7 else "最新"


def _fmt(v: float) -> str:
    return f"{v:g}"


def _simulator_page(cfg: dict, data: dict, cats: list) -> dict:
    """控除上限額シミュレーター。税制の年は寄付する年(取得日の年)。"""
    year = int((data.get("fetched_at") or "2026")[:4])
    picks = []
    for c in cats:
        r = data["categories"].get(c["id"], {})
        d = 0 if (c["unit_kind"] == "count" or c.get("scale")) else 1
        items = [{"name": it["name"][:60], "price": it["price"], "per10k": it["per10k"],
                  "per10k_s": f"{it['per10k']:,.{d}f}", "qty_s": f"{it['qty']:g}",
                  "town": it.get("town") or it["shop"], "url": it["url"]}
                 for it in r.get("items", [])]
        # 上限額ごとの1位だけ分かればよいので「より安くて量コスパが上の品が無いもの」だけ残す(ページを軽く)
        front, best = [], -1.0
        for it in sorted(items, key=lambda x: (x["price"], -x["per10k"])):
            if it["per10k"] > best:
                front.append(it); best = it["per10k"]
        items = front
        if items:
            picks.append({"name": c["name"], "emoji": c["emoji"], "unit": c["unit"], "items": items})
    qa = [
        ("控除上限額とは何ですか？", "自己負担2,000円だけで、残りが所得税・住民税から控除される寄付額の上限です。超えた分は自己負担になります。"),
        ("年収はいつの分で計算しますか？", f"寄付する年（{year}年）の1月〜12月の給与収入で決まります。見込みで計算し、年末に近づいたら源泉徴収票の金額で確認すると安全です。"),
        ("共働きの場合はどうなりますか？", "配偶者の年収が123万円を超える場合は「配偶者控除なし」を選んでください。夫婦それぞれが自分の年収で上限額を計算します。"),
        ("住宅ローン控除があると上限は下がりますか？", "住宅ローン控除が所得税から引ききれない場合などは、上限額が下がることがあります。このシミュレーターでは考慮していないので、目安として使ってください。"),
        ("ワンストップ特例と確定申告で上限額は変わりますか？", "上限額そのものは変わりません。寄付先が5自治体以内で確定申告が不要な会社員なら、ワンストップ特例で手続きできます。"),
    ]
    faq = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in qa]}
    return {"path": "simulator/index.html", "template": "simulator.html",
            "title": f"ふるさと納税 控除上限額シミュレーター【{year}年版】年収と家族構成ですぐ計算",
            "description": f"{year}年の税制(基礎控除・給与所得控除の改正)に対応。年収と家族構成を入れるだけで、ふるさと納税の控除上限額の目安を計算し、上限内で量が一番多い返礼品も表示します。",
            "context": {"cats": cats, "tax_year": year, "picks": picks, "faq": faq}}


def pages(cfg: dict, data: dict) -> list[dict]:
    # カテゴリはレビュー件数の合計が多い順(=人気順)。データが無いものは最後
    pop = {k: v.get("popularity", 0) for k, v in data.get("categories", {}).items()}
    order = {c["id"]: i for i, c in enumerate(cfg["categories"])}
    cats = sorted(cfg["categories"], key=lambda c: (-pop.get(c["id"], -1), order[c["id"]]))
    ps = [{"path": "index.html", "template": "index.html", "context": {"cats": cats},
           "title": f"ふるさと納税 コスパランキング｜寄付1万円あたりの量で比較【{_ym(data)}】"}]
    for c in cats:
        r = data["categories"].get(c["id"], {"items": []})
        ym = _ym(data)
        top = r["items"][0] if r.get("items") else None
        desc = f"楽天ふるさと納税の{c['name']}{r.get('ranked', 0)}件を「寄付1万円あたりの量」で比較。"
        if top:
            desc += f"1位は{top['town'] or top['shop']}の{_fmt(top['qty'])}{c['unit']}・寄付{top['price']:,}円。"
        desc += "毎日自動更新。"
        ps.append({
            "path": f"c/{c['id']}/index.html",
            "template": "category.html",
            "title": f"{c['name']}のふるさと納税 量コスパランキング【{ym}】",
            "description": desc,
            "context": {"cats": cats, "cat": c, "rank": r, "ym": ym, "jsonld": {
                "@context": "https://schema.org", "@type": "ItemList",
                "name": f"{c['name']}のふるさと納税 量コスパランキング",
                "itemListElement": [
                    {"@type": "ListItem", "position": i + 1, "name": it["name"][:100], "url": it["url"]}
                    for i, it in enumerate(r.get("items", [])[:10])]}},
        })
    ps.append(_simulator_page(cfg, data, cats))
    ps.append({"path": "about/index.html", "template": "about.html", "title": "このサイトについて・計算方法",
               "context": {"cats": cats}, "changefreq": "monthly"})
    return ps

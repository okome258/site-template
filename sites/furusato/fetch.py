"""ふるさと納税 量コスパ比較: 楽天市場から返礼品を集め、寄付1万円あたりの量でランキングする。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from core.qty import parse_quantity
from core.rakuten import Rakuten, RakutenError

TAX_TABLE_YEAR = 2026  # シミュレーターの税制(国税庁 No.1199/No.1410)を確認した年

# 楽天ふるさと納税の公式ジャンル別ランキング(総合ページの「通常ランキング」枠に並べる)
GENRE_RANKINGS = [("meat", "肉"), ("seafood", "海鮮"), ("rice", "お米"), ("fruit", "フルーツ"),
                  ("sweet", "スイーツ"), ("beer", "ビール"), ("sake", "お酒"), ("daily", "日用品"),
                  ("appliance", "家電"), ("travel-coupon", "旅行")]
FURUSATO_SHOP = re.compile(r"^f\d{6}")
PREF = re.compile(r"^(北海道|東京都|京都府|大阪府|.{2,3}県)")


from core.japan import SLUGS, TILES, pref_of  # noqa: E402

def _town(shop: str) -> str:
    """ショップ名「福井県小浜市」→「小浜市」(他サイト検索用)。形が違えばそのまま。"""
    s = re.sub(r"[\(（].*$", "", shop).strip()
    s2 = PREF.sub("", s)
    m = re.match(r"(.{1,8}?[市町村区])(?:$|[\s　_\-])", s2)
    return m.group(1) if m else s  # 楽天ふるさと納税の自治体ショップ(f + 自治体コード)


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


HISTORY_KEEP_DAYS = 400  # この日数見かけなかった返礼品は履歴から消す(ファイルを太らせない)


def update_history(path: Path, out: dict, today: str) -> None:
    """返礼品ごとに寄付額と内容量の記録を残し、前回から変わったものに印をつける。

    履歴は値が変わった日だけ追記する(毎日同じ値なら増えない)。
    item["change"] = {"price": 旧寄付額, "qty": 旧内容量, "since": 変わる前の記録日, "per10k_pct": 量コスパの増減%}
    """
    hist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for cid, r in out.items():
        for it in r.get("items", []):
            h = hist.setdefault(it["code"], {"cat": cid, "first": today, "h": []})
            h["cat"], h["name"], h["last"] = cid, it["name"][:80], today
            rec = [today, it["price"], it["qty"]]
            if not h["h"] or h["h"][-1][1:] != rec[1:]:
                h["h"].append(rec)
            if len(h["h"]) >= 2:
                d0, p0, q0 = h["h"][-2]
                old = q0 / p0 * 10000 if p0 else 0
                it["change"] = {"price": p0, "qty": q0, "since": d0, "changed": h["h"][-1][0],
                                "per10k_pct": round((it["per10k"] / old - 1) * 100) if old else None}
    cut = (datetime.fromisoformat(today) - timedelta(days=HISTORY_KEEP_DAYS)).date().isoformat()
    hist = {k: v for k, v in hist.items() if v.get("last", today) >= cut}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


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
    today = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    update_history(cfg["_dir"] / "data" / "history.json", out, today)
    # 量ではない「通常の人気ランキング」(楽天公式)への入口。アフィリエイトリンクにしておく
    rk = "https://event.rakuten.co.jp/furusato/ranking/"
    links = {"_total": api.affiliate_link(rk)}
    for cat in cfg["categories"]:
        slug = cat.get("rakuten_ranking")
        if slug:
            links[cat["id"]] = api.affiliate_link(f"{rk}{slug}/")
    for slug, label in GENRE_RANKINGS:
        links["g_" + slug] = api.affiliate_link(f"{rk}{slug}/")
    return {"categories": out, "errors": errors[:10], "ranking_links": links}


def _ym(data: dict) -> str:
    s = data.get("fetched_at", "")
    return f"{int(s[:4])}年{int(s[5:7])}月最新" if len(s) >= 7 else "最新"


def _fmt(v: float) -> str:
    return f"{v:g}"


def _simulator_page(cfg: dict, data: dict, cats: list) -> dict:
    """控除上限額シミュレーター。税制の年は寄付する年(取得日の年)。"""
    year = int((data.get("fetched_at") or "2026")[:4])
    # 税制の表を確認済みの最新年。新しい年の表を入れたらここを上げる(毎年1月に要確認)
    table_year = TAX_TABLE_YEAR
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
            "context": {"cats": cats, "tax_year": year, "table_year": table_year, "picks": picks, "faq": faq}}


AUPAY_A8 = "https://px.a8.net/svt/ejp?a8mat=4BECH2+BOC3EQ+54OC+5YJRM"
AUPAY_A8_IMG = "https://www15.a8.net/0.gif?a8mat=4BECH2+BOC3EQ+54OC+5YJRM"


def _portals_page(data: dict) -> dict:
    """ポイント禁止後に各ポータルがやっていること(sites/furusato/portals.yaml を手で更新する)。"""
    src = yaml.safe_load((Path(__file__).parent / "portals.yaml").read_text(encoding="utf-8"))
    portals = []
    for p in src.get("portals", []):
        p = dict(p)
        if p["id"] == "rakuten":
            p["aff"] = (data.get("ranking_links") or {}).get("_total")
        elif p["id"] == "aupay":
            p["aff"], p["aff_img"] = AUPAY_A8, AUPAY_A8_IMG
        portals.append(p)
    checked = max((str(p.get("checked", "")) for p in portals), default="")
    return {"path": "portals/index.html", "template": "portals.html",
            "title": "ふるさと納税サイト比較｜ポイント禁止後の代わりの特典とメリット・デメリット",
            "description": "2025年10月のポイント禁止後、楽天・ふるなび・au PAY・Yahoo!・ふるさとチョイス・さとふるがポイントの代わりにやっていること(決済の増量・カード還元など)を、メリット・デメリット付きで比較。",
            "context": {"portals": portals, "checked": checked}, "changefreq": "weekly"}


def _season(cfg: dict, data: dict) -> dict | None:
    month = int((data.get("fetched_at") or "2026-10")[5:7])
    for s in cfg.get("seasons", []):
        if month in s["months"]:
            return s
    return None



TOP_N_MAP = 10  # 地図で数える順位(各カテゴリの上位何位まで)


def _by_pref(data: dict, cats: list) -> dict:
    """都道府県 → そのカテゴリで最上位の返礼品の一覧(順位つき)"""
    out: dict[str, list] = {}
    for c in cats:
        seen = set()
        for i, it in enumerate(data["categories"].get(c["id"], {}).get("items", [])):
            p = pref_of(it.get("shop"))
            if p and p not in seen:
                seen.add(p)
                out.setdefault(p, []).append({"cat": c, "it": it, "rank": i + 1})
    for rows in out.values():
        rows.sort(key=lambda r: (r["rank"], -r["it"].get("vs_median", 0) or 0))
    return out


def pages(cfg: dict, data: dict) -> list[dict]:
    allc = cfg["categories"]
    byid = {c["id"]: c for c in allc}
    top_ids = [i for i in cfg.get("top", []) if i in byid]
    # 並び: 定番トップ → 残りは設定順(グループごとに見せる)
    cats = [byid[i] for i in top_ids] + [c for c in allc if c["id"] not in top_ids]
    tops = [byid[i] for i in top_ids]
    others = [c for c in allc if c["id"] not in top_ids]
    groups = {}
    for c in others:
        groups.setdefault(c.get("group", "その他"), []).append(c)
    season = _season(cfg, data)
    if season:
        season = {**season, "cats": [byid[i] for i in season["cats"] if i in byid]}
    bp = _by_pref(data, cats)
    pref_counts = {p: sum(1 for r in rows if r["rank"] <= TOP_N_MAP) for p, rows in bp.items()}
    nav = {"tops": tops, "others": others, "groups": list(groups.items()), "season": season,
           "pref_counts": pref_counts, "top_n_map": TOP_N_MAP}
    ps = [{"path": "index.html", "template": "index.html", "context": {"cats": cats, "nav": nav, "genres": GENRE_RANKINGS},
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
            "context": {"cats": cats, "nav": nav, "cat": c, "rank": r, "ym": ym, "jsonld": {
                "@context": "https://schema.org", "@type": "ItemList",
                "name": f"{c['name']}のふるさと納税 量コスパランキング",
                "itemListElement": [
                    {"@type": "ListItem", "position": i + 1, "name": it["name"][:100], "url": it["url"]}
                    for i, it in enumerate(r.get("items", [])[:10])]}},
        })
    sim = _simulator_page(cfg, data, cats)
    sim["context"]["nav"] = nav
    ps.append(sim)
    pt = _portals_page(data)
    pt["context"].update({"cats": cats, "nav": nav})
    ps.append(pt)
    ps.append({"path": "other/index.html", "template": "other.html", "title": "その他のカテゴリ",
               "description": "日用品・果物・麺・飲み物など、定番以外のふるさと納税の返礼品も寄付1万円あたりの量で比較。",
               "context": {"cats": cats, "nav": nav}})
    for p in TILES:
        # 全47県のページを常に作る(日によってページが消えないように)
        n = pref_counts.get(p, 0)
        allrows = bp.get(p, [])
        rows = [r for r in allrows if r["rank"] <= TOP_N_MAP]
        near = [] if rows else allrows[:5]  # 上位に無い県は、順位が良い順に最大5件
        if rows:
            names = "・".join(dict.fromkeys(r["cat"]["name"] for r in rows[:5]))
            desc = f"楽天ふるさと納税で{p}の返礼品が量コスパ上位{TOP_N_MAP}位に入ったカテゴリは{n}つ({names}など)。"
        else:
            desc = f"楽天ふるさと納税の{p}の返礼品を、寄付1万円あたりの量で比べたときの順位。"
        ps.append({"path": f"pref/{SLUGS[p]}/index.html", "template": "pref.html",
                   "title": f"{p}のふるさと納税 量コスパ返礼品【{_ym(data)}】",
                   "description": desc + "毎日自動更新。",
                   "context": {"cats": cats, "nav": nav, "pref": p, "rows": rows, "near": near}})
    ps.append({"path": "about/index.html", "template": "about.html", "title": "このサイトについて・計算方法",
               "context": {"cats": cats, "nav": nav}, "changefreq": "monthly"})
    return ps

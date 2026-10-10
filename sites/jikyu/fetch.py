"""業界別 実質時給ランキング。

厚生労働省「賃金構造基本統計調査」(一般労働者・産業大分類)を e-Stat API で取り、
業界ごとに 年収 =(きまって支給する現金給与額 × 12 + 年間賞与その他特別給与額)、
実質時給 = 年収 ÷((所定内実労働時間数 + 超過実労働時間数)× 12)を出す。

表のID(statsDataId)は年ごとに変わるので決め打ちせず、毎回 getStatsList で探し、
中身(表章項目・産業名)を確かめてから使う。どの表を使ったかは data に残す。
"""

from __future__ import annotations

import re

from core import industries
from core.estat import EStat

TOTAL_WORDS = ("計", "総数", "合計", "全国")
ALL_INDUSTRY = ("産業計", "全産業", "産業計(民営)")


# ---------------- 表を読む ----------------
def find_item_class(classes: dict, item_names: dict) -> tuple[str | None, dict]:
    """表章項目(年収・労働時間など)が入っている分類と、項目キー → コードの対応を返す。"""
    best = (None, {})
    for cid, c in classes.items():
        found = {}
        for key, want in item_names.items():
            w = industries.norm(want)
            exact = [code for code, nm in c["items"].items() if industries.norm(nm) == w]
            # 「年齢」のような短い名前は完全一致だけ(「年齢階級」に当てないため)
            loose = [] if len(w) <= 4 else [code for code, nm in c["items"].items()
                                             if w in industries.norm(nm)]
            if exact or loose:
                found[key] = (exact or loose)[0]
        if len(found) > len(best[1]):
            best = (cid, found)
    return best


def find_industry_class(classes: dict) -> tuple[str | None, dict]:
    """産業の分類と、コード → 業界(マスタ)の対応。産業計は "_all"。"""
    best = (None, {})
    for cid, c in classes.items():
        m = {}
        for code, nm in c["items"].items():
            if re.sub(r"^[A-Z]\d*", "", industries.norm(nm)) in ALL_INDUSTRY:
                m[code] = "_all"
                continue
            ind = industries.match(nm)
            if ind and ind["slug"] not in m.values():
                m[code] = ind["slug"]
        if len(m) > len(best[1]):
            best = (cid, m)
    return best


def total_code(c: dict, prefer: list[str] = ()) -> str:
    """性別・企業規模・年齢階級などの「計」のコード。prefer の名前があればそれ、なければ先頭。"""
    for want in prefer:
        for code in c["order"]:
            if industries.norm(c["items"][code]) == industries.norm(want):
                return code
    hits = [code for code in c["order"]
            if any(industries.norm(c["items"][code]).split("(")[0].endswith(w) for w in TOTAL_WORDS)]
    if hits:
        lv1 = [h for h in hits if str(c["level"].get(h, "1")) in ("", "1")]
        return (lv1 or hits)[0]
    return c["order"][0]


def inspect(cfg: dict, meta: dict) -> dict | None:
    """表が使えるか確かめ、使えるなら絞り込み条件を返す。"""
    classes = meta["classes"]
    item_cls, items = find_item_class(classes, cfg["items"])
    ind_cls, inds = find_industry_class(classes)
    if not item_cls or not ind_cls or item_cls == ind_cls:
        return None
    if any(k not in items for k in cfg["required"]):
        return None
    if sum(1 for v in inds.values() if v != "_all") < 10:
        return None
    fixed = {cid: total_code(c, cfg.get("fixed_prefer", [])) for cid, c in classes.items()
             if cid not in (item_cls, ind_cls, "time")}
    return {"item_cls": item_cls, "items": items, "ind_cls": ind_cls, "inds": inds,
            "fixed": fixed, "n_classes": len(classes)}


def param_name(cid: str) -> str:
    """分類ID → 絞り込みパラメータ名(tab→cdTab, cat01→cdCat01, area→cdArea)。"""
    return "cd" + cid[0].upper() + cid[1:]


def metrics(raw: dict) -> dict:
    m = dict(raw)
    mo, bo = raw.get("monthly"), raw.get("bonus")
    hs, ho = raw.get("hours_sched"), raw.get("hours_over")
    if None not in (mo, bo):
        m["annual_man"] = round((mo * 12 + bo) / 10, 1)          # 千円 → 万円
    if None not in (mo, bo, hs, ho) and hs + ho > 0:
        m["hours_month"] = round(hs + ho, 1)
        m["hourly"] = round((mo * 12 + bo) * 1000 / ((hs + ho) * 12))  # 円
    return m


def time_years(classes: dict) -> dict[str, str]:
    """時間軸の分類から {西暦年: コード}。名前に4桁の年がなければコードの先頭4桁。"""
    out = {}
    tc = classes.get("time")
    if not tc:
        return out
    for code in tc["order"]:
        m = re.search(r"(19|20)\d{2}", tc["items"][code]) or re.match(r"(19|20)\d{2}", code)
        if m:
            out.setdefault(m.group(0), code)
    return out


def extract(rows: list[dict], plan: dict, tcode: str | None) -> dict:
    """値の一覧から、ある年の業界ごとの数値を組み立てる。"""
    code_to_key = {v: k for k, v in plan["items"].items()}
    out: dict[str, dict] = {}
    for r in rows:
        if r["value"] is None:
            continue
        if any(r.get(cid) not in (None, code) for cid, code in plan["fixed"].items()):
            continue
        if tcode and r.get("time") not in (None, tcode):
            continue
        slug = plan["inds"].get(r.get(plan["ind_cls"]))
        key = code_to_key.get(r.get(plan["item_cls"]))
        if slug and key:
            out.setdefault(slug, {})[key] = r["value"]
    return {slug: metrics(raw) for slug, raw in out.items()}


def is_candidate(ec: dict, title: str) -> bool:
    """産業を並べて比べられる表だけ(産業別に1表ずつ分かれた表は除く)。"""
    if any(x in title for x in ec["title_exclude"]):
        return False
    return any(x in title for x in ec["title_require"])


# ---------------- 取得 ----------------
DEBUG: list = []
WATCH: list = []


def catalog_files(api, ec: dict, latest: int) -> list[dict]:
    """e-Stat のファイル(Excel)一覧から、最新年より後に公開されたものを探す。"""
    out = []
    for w in ec.get("catalog_words", ["一般労働者 産業", "産業大分類"]):
        try:
            body = api._get("getDataCatalog", "GET_DATA_CATALOG", statsCode=ec["stats_code"],
                            searchWord=w, limit=1000)
        except Exception as e:
            out.append({"word": w, "error": str(e)})
            continue
        cats = body.get("DATA_CATALOG_LIST_INF", {}).get("DATA_CATALOG_INF") or []
        cats = cats if isinstance(cats, list) else [cats]
        n = 0
        for c in cats:
            ds = c.get("DATASET", {})
            res = (c.get("RESOURCES") or {}).get("RESOURCE") or []
            res = res if isinstance(res, list) else [res]
            for r in res:
                n += 1
                rd = str(r.get("RELEASE_DATE", ""))
                if rd[:4].isdigit() and int(rd[:4]) <= latest + 1:
                    continue
                t = r.get("TITLE", {})
                out.append({"word": w, "id": r.get("@id"), "release": rd, "format": r.get("FORMAT"),
                            "url": r.get("URL"),
                            "survey": str(ds.get("SURVEY_DATE", "")),
                            "dataset": str((ds.get("TITLE") or {}).get("NAME", ""))[:80],
                            "name": str(t.get("NAME", ""))[:100], "no": str(t.get("TABLE_NO", ""))})
        out.append({"word": w, "n_resources": n})
    return out[:400]


def find_newer(api, ec: dict, latest: int) -> list[dict]:
    """使っている最新年より新しい年の表があるかを広く探して記録する(取り込みは別)。

    DB表(全年まとめ)は更新が遅いので、新しい年が年ごとの表で先に出ていないかを見張る。
    見つかったら Actions の警告に出し、data/debug.json に表題とIDを残す。
    """
    found: dict[str, dict] = {}
    stats = []
    for w in ec.get("watch_words", ["一般労働者", "産業大分類", "産業"]):
        try:
            tables = api.list_tables(ec["stats_code"], w, limit=2000)
        except Exception as e:  # 見張りの失敗でサイト更新は止めない
            print(f"::warning::新しい年の表の確認に失敗: {e}")
            continue
        ods = sorted({t["open_date"][:7] for t in tables})
        stats.append({"word": w, "n": len(tables), "open_dates": ods[-6:]})
        for t in tables:
            # 調査年(survey_date)は空のことが多いので、公開日が「最新年の翌年以降」の表を拾う
            # (例: 2023年分の公開は2024年3月なので、2025年以降に出た表は2024年分以降の可能性)
            od = t["open_date"][:4]
            if not od.isdigit() or int(od) <= latest + 1:
                continue
            if any(x in t["title"] for x in ("短時間", "職種", "都道府県")):
                continue
            found[t["id"]] = {k: t[k] for k in ("id", "title", "survey_date", "open_date")}
    print(f"表の検索結果: {stats}")
    WATCH.extend(stats)
    WATCH.append({"catalog": catalog_files(api, ec, latest)})
    out = sorted(found.values(), key=lambda t: t["id"], reverse=True)[:200]
    if out:
        print(f"::warning::{latest}年より新しい表が {len(out)}件あります(まだ取り込めていません)。"
              "data/debug.json の newer_tables を確認")
    return out


def _dump(cfg: dict, obj: dict) -> None:
    """どの表をどう読んだかを data/debug.json に残す(取得に失敗しても残る)。"""
    import json
    path = cfg["_dir"] / "data" / "debug.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def fetch(cfg: dict) -> dict:
    api = EStat()
    if not api.ready:
        print("::warning::ESTAT_APP_ID が未設定なので、準備中ページだけ生成します")
        return {"status": "no_key", "years": []}

    ec = cfg["estat"]
    tables = {}
    for w in ec["search_words"]:
        for t in api.list_tables(ec["stats_code"], w):
            if is_candidate(ec, t["title"]):
                tables[t["id"]] = t
    cands = sorted(tables.values(), key=lambda t: t["id"], reverse=True)[: ec["max_candidates"]]
    print(f"候補の表: {len(tables)}件(中身を確認するのは {len(cands)}件)")

    # 表ごとに中身を確かめ、年ごとに一番よい表を選ぶ(業界が多くそろう → 分類が少ない → IDが新しい)
    checked, best = [], {}
    for t in cands:
        meta = api.get_data(t["id"], limit=1)
        plan = inspect(cfg, meta)
        yrs = time_years(meta["classes"])
        if not yrs and t["survey_date"][:4].isdigit():
            yrs = {t["survey_date"][:4]: None}
        checked.append({"id": t["id"], "title": t["title"][:80], "ok": bool(plan),
                        "years": sorted(yrs)[:1] + sorted(yrs)[-1:],
                        "classes": {cid: {"name": c["name"], "n": len(c["order"]),
                                          "head": [c["items"][x] for x in c["order"][:6]]}
                                    for cid, c in meta["classes"].items()},
                        "plan": plan and {k: plan[k] for k in ("item_cls", "ind_cls", "fixed", "items")}})
        if not plan:
            continue
        n_ind = sum(1 for v in plan["inds"].values() if v != "_all")
        score = (n_ind, -plan["n_classes"], t["id"])
        for y, code in yrs.items():
            if y not in best or score > best[y][0]:
                best[y] = (score, t, plan, code)

    want = sorted((y for y in best if int(y) >= ec.get("min_year", 0)), reverse=True)[: ec["years"]]
    by_table: dict[str, list[str]] = {}
    for y in want:
        by_table.setdefault(best[y][1]["id"], []).append(y)

    years = []
    for tid, ys in by_table.items():
        _, t, plan, _ = best[ys[0]]
        filters = {param_name(plan["item_cls"]): ",".join(plan["items"].values())}
        filters.update({param_name(cid): code for cid, code in plan["fixed"].items()})
        codes = [best[y][3] for y in ys if best[y][3]]
        if codes:
            filters["cdTime"] = ",".join(codes)
        data = api.get_data(tid, **filters)
        DEBUG.append({"id": tid, "filters": filters, "n_values": len(data["values"]),
                      "sample": data["values"][:5]})
        for y in ys:
            rows = extract(data["values"], plan, best[y][3])
            if sum(1 for s in rows if s != "_all") < 10:
                print(f"::warning::{y}年: 値が少なすぎるので使わない({tid})")
                continue
            print(f"{y}年: {tid} {t['title']} → {len(rows)}業界")
            years.append({"year": y, "table": {"id": tid, "title": t["title"], "open_date": t["open_date"]},
                          "all": rows.pop("_all", {}), "rows": rows})
    years.sort(key=lambda y: y["year"], reverse=True)
    newer = find_newer(api, ec, int(years[0]["year"]) if years else ec.get("min_year", 2020))
    _dump(cfg, {"checked": checked, "fetched": DEBUG, "newer_tables": newer, "watch": WATCH})
    if not years:
        _dump(cfg, {"checked": checked, "fetched": DEBUG})
        raise RuntimeError("賃金構造基本統計調査の表が1年分も取れませんでした")
    return {"status": "ok", "years": years, "checked_tables": checked}


# ---------------- ページ ----------------
def pages(cfg: dict, data: dict) -> list[dict]:
    about = {"path": "about/index.html", "template": "about.html", "title": "このサイトについて・計算方法",
             "changefreq": "monthly"}
    years = data.get("years") or []
    if data.get("status") != "ok" or not years:
        return [{"path": "index.html", "template": "index.html", "noindex": True,
                 "context": {"ready": False}}, {**about, "noindex": True}]

    latest = years[0]
    prev = years[1] if len(years) > 1 else None
    master = industries.by_slug()
    ranking = sorted(
        ({"ind": master[s], **m,
          "diff": (m.get("hourly") - prev["rows"][s]["hourly"])
          if prev and prev["rows"].get(s, {}).get("hourly") and m.get("hourly") else None}
         for s, m in latest["rows"].items() if m.get("hourly")),
        key=lambda r: -r["hourly"])
    history = {s: [{"year": y["year"], **y["rows"][s]} for y in reversed(years) if s in y["rows"]]
               for s in latest["rows"]}
    common = {"latest": latest, "prev": prev, "ranking": ranking, "industries": master,
              "max_hourly": max(r["hourly"] for r in ranking), "ready": True}

    out = [{"path": "index.html", "template": "index.html",
            "title": f"業界別 実質時給ランキング{latest['year']}｜年収÷労働時間で比べる",
            "context": common}]
    for i, r in enumerate(ranking, 1):
        s = r["ind"]["slug"]
        out.append({
            "path": f"gyokai/{s}/index.html", "template": "industry.html",
            "title": f"{r['ind']['short']}業界の実質時給・年収・残業時間（{latest['year']}年）",
            "description": (f"{r['ind']['name']}の実質時給は{r['hourly']:,.0f}円で全{len(ranking)}業界中{i}位。"
                            f"平均年収{r.get('annual_man', 0):,.0f}万円、月の労働時間{r.get('hours_month', 0)}時間。"
                            "賃金構造基本統計調査から計算し、年ごとの推移も掲載。"),
            "context": {**common, "row": r, "rank": i, "hist": history[s]},
        })
    out.append({**about, "context": common})
    return out

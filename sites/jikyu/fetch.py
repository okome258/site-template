"""業界別 実質時給ランキング。

厚生労働省「賃金構造基本統計調査」(一般労働者・産業大分類)を e-Stat API で取り、
業界ごとに 年収 =(きまって支給する現金給与額 × 12 + 年間賞与その他特別給与額)、
実質時給 = 年収 ÷((所定内実労働時間数 + 超過実労働時間数)× 12)を出す。

表のID(statsDataId)は年ごとに変わるので決め打ちせず、毎回 getStatsList で探し、
中身(表章項目・産業名)を確かめてから使う。どの表を使ったかは data に残す。
"""

from __future__ import annotations

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
            if industries.norm(nm) in ALL_INDUSTRY:
                m[code] = "_all"
                continue
            ind = industries.match(nm)
            if ind and ind["slug"] not in m.values():
                m[code] = ind["slug"]
        if len(m) > len(best[1]):
            best = (cid, m)
    return best


def total_code(c: dict) -> str:
    """性別・企業規模・年齢階級などの「計」のコード。見つからなければ先頭。"""
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
    fixed = {cid: total_code(c) for cid, c in classes.items()
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


def extract(meta_classes: dict, rows: list[dict], plan: dict, year: str) -> dict:
    """値の一覧から、業界ごとの数値を組み立てる。"""
    times = meta_classes.get("time", {"items": {}, "order": []})
    tcode = next((c for c in times["order"] if year in times["items"][c]), None)
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


# ---------------- 取得 ----------------
def fetch(cfg: dict) -> dict:
    api = EStat()
    if not api.ready:
        print("::warning::ESTAT_APP_ID が未設定なので、準備中ページだけ生成します")
        return {"status": "no_key", "years": []}

    ec = cfg["estat"]
    tables = {}
    for w in ec["search_words"]:
        for t in api.list_tables(ec["stats_code"], w):
            if not any(x in t["title"] for x in ec["title_exclude"]):
                tables[t["id"]] = t
    by_year: dict[str, list[dict]] = {}
    for t in tables.values():
        y = t["survey_date"][:4]
        if y.isdigit():
            by_year.setdefault(y, []).append(t)
    print(f"候補の表: {len(tables)}件 / 年: {sorted(by_year)}")

    years = []
    for y in sorted(by_year, reverse=True):
        if len(years) >= ec["years"]:
            break
        best = None
        cands = sorted(by_year[y], key=lambda t: len(t["title"]))[: ec["max_candidates"]]
        for t in cands:
            meta = api.get_data(t["id"], limit=1)
            plan = inspect(cfg, meta)
            if plan and (best is None or plan["n_classes"] < best[1]["n_classes"]):
                best = (t, plan, meta)
        if not best:
            print(f"::warning::{y}年: 使える表が見つからない(候補 {len(cands)}件)")
            continue
        t, plan, meta = best
        filters = {param_name(plan["item_cls"]): ",".join(plan["items"].values())}
        filters.update({param_name(cid): code for cid, code in plan["fixed"].items()})
        data = api.get_data(t["id"], **filters)
        rows = extract(meta["classes"], data["values"], plan, y)
        if sum(1 for s in rows if s != "_all") < 10:
            print(f"::warning::{y}年: 値が少なすぎるので使わない({t['id']})")
            continue
        print(f"{y}年: {t['id']} {t['title']} → {len(rows)}業界")
        years.append({"year": y, "table": {"id": t["id"], "title": t["title"],
                                           "open_date": t["open_date"]},
                      "all": rows.pop("_all", {}), "rows": rows})
    if not years:
        raise RuntimeError("賃金構造基本統計調査の表が1年分も取れませんでした")
    return {"status": "ok", "years": years}


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

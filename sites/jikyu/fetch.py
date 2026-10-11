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
            "fixed": fixed, "n_classes": len(classes), "ind_names": classes[ind_cls]["items"]}


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


def strip_code(name: str) -> str:
    """「Ｉ５０ 各種商品卸売業」「I50各種商品卸売業」→「各種商品卸売業」"""
    return re.sub(r"^[A-Z][0-9～~\-]*", "", industries.norm(name))


def focus_slug(cfg: dict, name: str) -> str | None:
    n = strip_code(name)
    for f in cfg.get("focus", []):
        w = industries.norm(f["name"])
        if n == w or (len(n) >= 4 and (n.startswith(w) or w.startswith(n))):
            return f["slug"]
    return None


def extract_focus(cfg: dict, rows: list[dict], plan: dict, tcode: str | None) -> dict:
    code_to_key = {v: k for k, v in plan["items"].items()}
    codes = {c: focus_slug(cfg, nm) for c, nm in plan["ind_names"].items()}
    out: dict[str, dict] = {}
    for r in rows:
        if r["value"] is None:
            continue
        if any(r.get(cid) not in (None, code) for cid, code in plan["fixed"].items()):
            continue
        if tcode and r.get("time") not in (None, tcode):
            continue
        slug = codes.get(r.get(plan["ind_cls"]))
        key = code_to_key.get(r.get(plan["item_cls"]))
        if slug and key:
            out.setdefault(slug, {})[key] = r["value"]
    return {slug: metrics(raw) for slug, raw in out.items() if "monthly" in raw}


def is_candidate(ec: dict, title: str) -> bool:
    """産業を並べて比べられる表だけ(産業別に1表ずつ分かれた表は除く)。"""
    if any(x in title for x in ec["title_exclude"]):
        return False
    return any(x in title for x in ec["title_require"])


# ---------------- 取得 ----------------
DEBUG: list = []


def _dump(cfg: dict, obj: dict) -> None:
    """どの表をどう読んだかを data/debug.json に残す(取得に失敗しても残る)。"""
    import json
    path = cfg["_dir"] / "data" / "debug.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def fetch_db_years(api, cfg: dict, checked: list) -> list[dict]:
    """e-Stat データベース(全年まとめの表)から業界別の年を取る。2020〜2023年はこちら。"""
    ec = cfg["estat"]
    tables = {}
    for w in ec["search_words"]:
        for t in api.list_tables(ec["stats_code"], w):
            if is_candidate(ec, t["title"]):
                tables[t["id"]] = t
    cands = sorted(tables.values(), key=lambda t: t["id"], reverse=True)[: ec["max_candidates"]]
    print(f"候補の表: {len(tables)}件(中身を確認するのは {len(cands)}件)")

    # 表ごとに中身を確かめ、年ごとに一番よい表を選ぶ(業界が多くそろう → 分類が少ない → IDが新しい)
    best = {}
    for t in cands:
        meta = api.get_data(t["id"], limit=1)
        plan = inspect(cfg, meta)
        yrs = time_years(meta["classes"])
        if not yrs and t["survey_date"][:4].isdigit():
            yrs = {t["survey_date"][:4]: None}
        checked.append({"id": t["id"], "title": t["title"][:80], "ok": bool(plan),
                        "years": sorted(yrs)[:1] + sorted(yrs)[-1:],
                        "plan": plan and {k: plan[k] for k in ("item_cls", "ind_cls", "fixed", "items")}})
        if not plan:
            continue
        n_ind = sum(1 for v in plan["inds"].values() if v != "_all")
        score = (n_ind, -plan["n_classes"], t["id"])
        for y, code in yrs.items():
            if y not in best or score > best[y][0]:
                best[y] = (score, t, plan, code)

    want = sorted((y for y in best if int(y) >= ec.get("min_year", 0)), reverse=True)
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
        DEBUG.append({"id": tid, "filters": filters, "n_values": len(data["values"])})
        for y in ys:
            rows = extract(data["values"], plan, best[y][3])
            if sum(1 for s in rows if s != "_all") < 10:
                print(f"::warning::{y}年: 値が少なすぎるので使わない({tid})")
                continue
            print(f"{y}年: {tid} {t['title']} → {len(rows)}業界")
            years.append({"year": y, "table": {"id": tid, "title": t["title"], "open_date": t["open_date"]},
                          "all": rows.pop("_all", {}), "rows": rows,
                          "focus": extract_focus(cfg, data["values"], plan, best[y][3])})
    return years


# ---- 2024年分以降: 年ごとの Excel ----
def load_cache(cfg: dict) -> dict:
    import json
    p = cfg["_dir"] / "data" / "xls_cache.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(cfg: dict, cache: dict) -> None:
    import json
    p = cfg["_dir"] / "data" / "xls_cache.json"
    p.write_text(json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")


def read_file(cache: dict, f: dict) -> list[dict]:
    """Excel 1ファイルを読み、シートごとの結果を返す。一度読んだファイルはキャッシュから。"""
    import xls
    key = f["id"] + "@" + f["release"]
    if key not in cache:
        book = xls.read_book(xls.download(f["url"]))
        cache[key] = [r for r in (xls.parse_sheet(rows) for rows in book.values()) if r]
        print(f"  Excel 読み込み: {f['name'][:50]} → {len(cache[key])}シート")
    return cache[key]


def xls_files(api, ec: dict, year: int, kind: str) -> list[dict]:
    """その年の Excel 一覧(kind: 産業大分類 / 都道府県別)。表題の年は和暦の全角数字。"""
    import re
    import xls
    word = f"令和{xls.zen(year - 2018)}年 一般労働者 {kind}"
    files = xls.catalog(api, ec["stats_code"], word)
    pat = re.compile(rf"一般労働者_{kind}_{year}年")
    return [f for f in files if pat.search(f["dataset"]) and f["url"]]


def fetch_xls_industry(api, cfg: dict, year: int, cache: dict) -> dict | None:
    ec = cfg["estat"]
    files = [f for f in xls_files(api, ec, year, "産業大分類")
             if f["name"].startswith("1_") and "産業計・産業別" in f["name"]]
    if not files:
        return None
    f = max(files, key=lambda x: x["release"])
    rows: dict[str, dict] = {}
    for sh in read_file(cache, f):
        lb = sh["labels"]
        if "民営" not in lb.get("民公区分", "民営") or "公営" in lb.get("民公区分", ""):
            continue
        name = lb.get("産業", "")
        slug = "_all" if industries.norm(name).endswith("産業計") else (industries.match(name) or {}).get("slug")
        if slug and slug not in rows:
            rows[slug] = metrics(sh["raw"])
    if sum(1 for s in rows if s != "_all") < 10:
        print(f"::warning::{year}年の Excel から業界が{len(rows)}件しか読めませんでした(形が変わった可能性)")
        return None
    return {"year": str(year), "source": "excel",
            "table": {"id": f["id"], "title": f["dataset"].split("_", 1)[-1] + "（Excel）",
                      "open_date": f["release"]},
            "all": rows.pop("_all", {}), "rows": rows}


def fetch_xls_focus(api, cfg: dict, year: int, cache: dict) -> dict:
    """産業中分類の Excel(大分類ごとに1ファイル、中分類ごとに1シート)から注目業種を読む。"""
    ec = cfg["estat"]
    want = {f["parent"] for f in cfg.get("focus", [])}
    out: dict[str, dict] = {}
    for f in xls_files(api, ec, year, "産業中分類"):
        if not f["name"].startswith("1_"):
            continue
        # ファイル名の大分類(例「D 建設業（D06～D08）」)で、注目業種が無いファイルは読まない
        parent = industries.match(re.sub(r"（.*$", "", f["name"].split("_")[-1]))
        if parent and parent["slug"] not in want:
            continue
        try:
            sheets = read_file(cache, f)
        except Exception as e:
            DEBUG.append({"focus_xls_error": f["name"][-20:], "year": year, "error": f"{type(e).__name__}: {str(e)[:150]}"})
            continue
        for sh in sheets:
            lb = sh["labels"]
            if "公営" in lb.get("民公区分", ""):
                continue
            slug = focus_slug(cfg, lb.get("産業", ""))
            if slug and slug not in out:
                out[slug] = metrics(sh["raw"])
    return out


PREF_ALIAS = {"東京": "東京都", "京都": "京都府", "大阪": "大阪府", "北海道": "北海道"}


def pref_name(name: str) -> str | None:
    from core.japan import TILES
    n = industries.norm(name)
    n = re.sub(r"^\d+", "", n)
    for cand in (n, PREF_ALIAS.get(n, ""), n + "県"):
        if cand in TILES:
            return cand
    return None


def fetch_xls_pref(api, cfg: dict, year: int, cache: dict) -> dict | None:
    ec = cfg["estat"]
    files = [f for f in xls_files(api, ec, year, "都道府県別")
             if f["name"].startswith("1_都道府県、年齢階級別") and "5～9人" not in f["name"]]
    rows = {}
    for f in files:
        try:
            sheets = read_file(cache, f)
        except Exception as e:  # 1ファイルの失敗で全体を止めない(翌日また取りにいく)
            DEBUG.append({"pref_xls_error": f["name"][-12:], "year": year, "error": f"{type(e).__name__}: {str(e)[:150]}"})
            continue
        for sh in sheets:
            lb = sh["labels"]
            if not industries.norm(lb.get("産業", "")).endswith("産業計"):
                continue
            p = pref_name(lb.get("都道府県", ""))
            if p and p not in rows:
                rows[p] = metrics(sh["raw"])
    if len(rows) < 40:
        if files:
            print(f"::warning::{year}年の都道府県 Excel が{len(rows)}県分しか読めませんでした")
        return None
    return {"year": str(year), "source": "excel", "rows": rows}


def fetch_db_pref(api, cfg: dict) -> list[dict]:
    """都道府県別の全年まとめの表(2020〜2023年)。"""
    ec = cfg["estat"]
    tabs = [t for t in api.list_tables(ec["stats_code"], "都道府県別 年齢階級別")
            if "一般_都道府県別_年齢階級別DB" in t["title"]]
    if not tabs:
        print("::warning::都道府県別のDB表が見つかりません")
        return []
    t = max(tabs, key=lambda x: x["id"])
    meta = api.get_data(t["id"], limit=1)
    classes = meta["classes"]
    item_cls, items = find_item_class(classes, cfg["items"])
    if not item_cls or any(k not in items for k in cfg["required"]) or "area" not in classes:
        print("::warning::都道府県別のDB表の形が想定と違います")
        return []
    prefer = list(cfg.get("fixed_prefer", [])) + ["産業計", "Ｔ１ 産業計", "T1 産業計"]
    fixed = {cid: total_code(c, prefer) for cid, c in classes.items()
             if cid not in (item_cls, "area", "time")}
    # 同じ名前の表章項目が年によって別コードで入っているので、同名のコードをまとめて取る
    tab = classes[item_cls]
    key_of = {}
    for k, code in items.items():
        want = industries.norm(tab["items"][code])
        for c2, nm in tab["items"].items():
            if industries.norm(nm) == want:
                key_of[c2] = k
    filters = {param_name(item_cls): ",".join(key_of)}
    filters.update({param_name(cid): code for cid, code in fixed.items()})
    data = api.get_data(t["id"], **filters)
    area = {code: pref_name(nm) for code, nm in classes["area"]["items"].items()}
    tyear = time_years(classes)
    code_year = {v: k for k, v in tyear.items()}
    acc: dict[str, dict[str, dict]] = {}
    for r in data["values"]:
        if r["value"] is None or any(r.get(cid) not in (None, c) for cid, c in fixed.items()):
            continue
        p, y, k = area.get(r.get("area")), code_year.get(r.get("time")), key_of.get(r.get(item_cls))
        if p and y and k:
            acc.setdefault(y, {}).setdefault(p, {}).setdefault(k, r["value"])
    DEBUG.append({"pref_db": t["id"], "fixed": fixed, "n_values": len(data["values"]),
                  "classes": {cid: {"name": c["name"], "n": len(c["order"]),
                                    "head": [c["items"][x] for x in c["order"][:5]]}
                              for cid, c in classes.items()},
                  "per_year": {y: len(v) for y, v in acc.items()}})
    return [{"year": y, "source": "db", "table": {"id": t["id"], "title": t["title"]},
             "rows": {p: metrics(raw) for p, raw in v.items()}}
            for y, v in sorted(acc.items(), reverse=True) if len(v) >= 40]


# ---- 職業別の就業者数(総務省 労働力調査・年次) ----
OCC_MAJOR = ["001", "002", "007", "011", "015", "021", "022", "023", "031", "032", "033"]
# AIの影響の受けやすさの目安(ILO「Generative AI and Jobs」の傾向を、日本の職業大分類に当てはめたもの)
#  3=高い(仕事の多くが置き換わりうる) 2=中(仕事の一部が変わる) 1=低い(体を使う・対人の仕事が中心)
AI_LEVEL = {"007": 3, "008": 3, "009": 3, "010": 3,
            "001": 2, "002": 2, "003": 2, "005": 2, "006": 2, "011": 2, "012": 2, "013": 2, "014": 2,
            "004": 1}


def fetch_occupations(api) -> dict | None:
    """職業別就業者数(2009年〜・年次・全国)。表が見つからなければ None。"""
    tid = "0003024268"
    try:
        body = api._get("getStatsList", "GET_STATS_LIST", searchWord="従業上の地位 職業別就業者数", limit=100)
        tabs = body.get("DATALIST_INF", {}).get("TABLE_INF") or []
        tabs = tabs if isinstance(tabs, list) else [tabs]
        from core.estat import parse_table_inf
        hits = [parse_table_inf(t) for t in tabs]
        hits = [t for t in hits if "年次" in t["statistics_name"] and "全国" in t["statistics_name"]
                and t["title"].startswith("従業上の地位，職業別就業者数") and "～）" in t["title"]]
        if hits:
            tid = max(hits, key=lambda t: t["open_date"])["id"]
    except Exception:
        pass
    meta = api.get_data(tid, limit=1)
    cl = meta["classes"]
    occ = next((c for c in cl if "職業" in cl[c]["name"]), None)
    if not occ:
        return None
    fixed = {cid: total_code(c, ["総数", "15歳以上"]) for cid, c in cl.items()
             if cid not in (occ, "time", "tab", "area")}
    filters = {param_name(cid): code for cid, code in fixed.items()}
    data = api.get_data(tid, **filters)
    code_year = {v: k for k, v in time_years(cl).items()}
    vals: dict[str, dict[str, float]] = {}
    for r in data["values"]:
        if r["value"] is None or any(r.get(cid) not in (None, code) for cid, code in fixed.items()):
            continue
        y = code_year.get(r.get("time"))
        if y:
            vals.setdefault(r[occ], {})[y] = r["value"]
    rows, parent = [], None
    for code in cl[occ]["order"]:
        name = cl[occ]["items"][code]
        if code in OCC_MAJOR:
            parent = code
        if code == "000":
            parent = None
        rows.append({"code": code, "name": name, "short": name.replace("従事者", ""),
                     "major": code in OCC_MAJOR, "parent": parent if code not in OCC_MAJOR else None,
                     "ai": AI_LEVEL.get(code, AI_LEVEL.get(parent or "", 1)) if code != "000" else None,
                     "values": vals.get(code, {})})
    DEBUG.append({"occ_table": tid, "filters": filters, "n_values": len(data["values"])})
    return {"table": {"id": tid, "title": meta["table"]["title"]}, "years": sorted(code_year.values()), "rows": rows}


def fetch_occ_by_industry(api, year: str) -> dict | None:
    """業界ごとの職業の内訳(労働力調査・産業，職業別就業者数・年次)。AIの影響を受けやすい仕事の割合を出す。"""
    tid = "0003024266"
    try:
        body = api._get("getStatsList", "GET_STATS_LIST", searchWord="産業 職業別就業者数", limit=100)
        tabs = body.get("DATALIST_INF", {}).get("TABLE_INF") or []
        tabs = tabs if isinstance(tabs, list) else [tabs]
        from core.estat import parse_table_inf
        hits = [parse_table_inf(t) for t in tabs]
        hits = [t for t in hits if "年次" in t["statistics_name"] and "全国" in t["statistics_name"]
                and t["title"].startswith("産業，職業別就業者数") and "～）" in t["title"]]
        if hits:
            tid = max(hits, key=lambda t: t["open_date"])["id"]
    except Exception:
        pass
    meta = api.get_data(tid, limit=1)
    cl = meta["classes"]
    occ = next((c for c in cl if "職業" in cl[c]["name"]), None)
    ind = next((c for c in cl if "産業" in cl[c]["name"]), None)
    if not occ or not ind:
        return None
    code_year = {v: k for k, v in time_years(cl).items()}
    ycode = next((c for c, y in code_year.items() if y == year), None) or max(code_year, key=lambda c: code_year[c])
    fixed = {}
    for cid, c in cl.items():
        if cid in (occ, ind, "time", "tab", "area"):
            continue
        prefer = ["総数", "就業者", "15歳以上"]
        fixed[cid] = total_code(c, prefer)
    filters = {param_name(cid): code for cid, code in fixed.items()}
    filters["cdTime"] = ycode
    data = api.get_data(tid, **filters)
    order = cl[occ]["order"]
    has_sub, parent = set(), None
    for code in order:
        if code in OCC_MAJOR:
            parent = code
        elif code != "000" and parent:
            has_sub.add(parent)
    leaves = [c for c in order if c != "000" and c not in has_sub]
    vals: dict[str, dict[str, float]] = {}
    for r in data["values"]:
        if r["value"] is None or any(r.get(cid) not in (None, code) for cid, code in fixed.items()):
            continue
        vals.setdefault(r[ind], {})[r[occ]] = r["value"]
    rows = []
    for icode, v in vals.items():
        tot = v.get("000")
        if not tot:
            continue
        lv = {3: 0.0, 2: 0.0, 1: 0.0}
        parent = None
        for code in order:
            if code in OCC_MAJOR:
                parent = code
            if code in leaves and v.get(code):
                lv[AI_LEVEL.get(code, AI_LEVEL.get(parent or "", 1))] += v[code]
        known = sum(lv.values())
        if known < tot * 0.6:   # 内訳が秘匿などで足りない業界は使わない
            continue
        rows.append({"code": icode, "name": cl[ind]["items"][icode], "total": tot,
                     "high": round(lv[3] / known, 3), "mid": round(lv[2] / known, 3), "low": round(lv[1] / known, 3),
                     "clerical": round(v.get("007", 0) / tot, 3)})
    DEBUG.append({"occ_ind_table": tid, "year": code_year.get(ycode), "n_values": len(data["values"]),
                  "industries": [r["name"] for r in rows]})
    return {"table": {"id": tid, "title": meta["table"]["title"]}, "year": code_year.get(ycode), "rows": rows}


def fetch(cfg: dict) -> dict:
    from datetime import date
    api = EStat()
    if not api.ready:
        print("::warning::ESTAT_APP_ID が未設定なので、準備中ページだけ生成します")
        return {"status": "no_key", "years": []}

    ec = cfg["estat"]
    checked: list = []
    years = fetch_db_years(api, cfg, checked)
    db_latest = max((int(y["year"]) for y in years), default=ec.get("min_year", 2020) - 1)

    # DB に入っていない新しい年は、年ごとの Excel から取る(毎年3月ごろ公表)
    cache = load_cache(cfg)
    for y in range(db_latest + 1, date.today().year + 1):
        try:
            got = fetch_xls_industry(api, cfg, y, cache)
        except Exception as e:  # Excel の失敗でサイト全体は止めない
            print(f"::warning::{y}年の Excel 取得に失敗: {type(e).__name__}")
            got = None
        if got:
            print(f"{y}年: Excel から {len(got['rows'])}業界")
            try:
                got["focus"] = fetch_xls_focus(api, cfg, y, cache)
            except Exception as e:
                print(f"::warning::{y}年の注目業種の取得に失敗: {type(e).__name__}")
                got["focus"] = {}
            print(f"{y}年: 注目業種 {len(got['focus'])}/{len(cfg.get('focus', []))}")
            years.append(got)
    years.sort(key=lambda y: y["year"], reverse=True)
    if not years:
        _dump(cfg, {"checked": checked, "fetched": DEBUG})
        raise RuntimeError("賃金構造基本統計調査の表が1年分も取れませんでした")

    # 都道府県別
    prefs = []
    try:
        prefs = fetch_db_pref(api, cfg)
        p_latest = max((int(p["year"]) for p in prefs), default=ec.get("min_year", 2020) - 1)
        for y in range(p_latest + 1, date.today().year + 1):
            got = fetch_xls_pref(api, cfg, y, cache)
            DEBUG.append({"pref_xls_year": y, "ok": bool(got)})
            if got:
                print(f"{y}年: 都道府県 Excel から {len(got['rows'])}県")
                prefs.append(got)
    except Exception as e:
        print(f"::warning::都道府県別の取得に失敗: {type(e).__name__}: {str(e)[:120]}")
    prefs.sort(key=lambda y: y["year"], reverse=True)
    save_cache(cfg, cache)

    newest = years[0]["year"]
    if int(newest) < date.today().year - 2 + (1 if date.today().month >= 4 else 0):
        print(f"::warning::最新が{newest}年のままです。新しい年の公表を確認してください")
    _dump(cfg, {"checked": checked, "fetched": DEBUG})
    occ = None
    try:
        occ = fetch_occupations(api)
        if occ:
            print(f"職業別就業者数: {occ['table']['id']} {len(occ['rows'])}職業 {occ['years'][:1]}〜{occ['years'][-1:]}")
            oi = fetch_occ_by_industry(api, occ["years"][-1])
            if oi:
                occ["by_industry"] = oi
                print(f"業界ごとの職業の内訳: {len(oi['rows'])}業界({oi['year']}年)")
    except Exception as e:
        print(f"::warning::職業別就業者数の取得に失敗: {type(e).__name__}: {str(e)[:120]}")
    return {"status": "ok", "years": years, "occ": occ, "prefs": prefs[: ec["years"]], "checked_tables": checked}


# ---------------- ページ ----------------
def pref_map(rank: list[dict]) -> str:
    """実質時給が高い県ほど濃く塗った日本のタイル地図(ふるさと納税サイトと同じ形)。押すと表の行へ。"""
    from markupsafe import Markup
    from core.japan import H, SLUGS, TILES, W, T, _rect, short
    n = len(rank)
    lv = {r["name"]: 4 - min(3, i * 4 // max(n, 1)) for i, r in enumerate(rank)}  # 上位1/4が濃い
    val = {r["name"]: r["hourly"] for r in rank}
    parts = []
    for p, (x, y, w, h) in TILES.items():
        name = short(p)
        fs = 4.2 if len(name) <= 2 else 3.2
        cx, cy = (x + w / 2) * T, (y + h / 2) * T + fs * 0.35
        tip = f"{p}: {val[p]:,.0f}円" if p in val else p
        parts.append(f'<a href="#pref-{SLUGS[p]}" class="lv{lv.get(p, 0)}"><title>{tip}</title>'
                     f'{_rect(x, y, w, h)}<text x="{cx:g}" y="{cy:g}" font-size="{fs}">{name}</text></a>')
    return Markup(f'<svg class="jp-big" viewBox="0 0 {W} {H}" role="img" aria-label="都道府県別の実質時給の地図">'
                  f'{"".join(parts)}</svg>')


def pref_slug(name: str) -> str:
    from core.japan import SLUGS
    return SLUGS.get(name, "")

def trend(points: list[tuple[int, float]], to_year: int) -> dict | None:
    """年と値の並びに直線を当てはめ(最小二乗法)、to_year の値を出す。3年分以上ないときは出さない。"""
    pts = [(x, y) for x, y in points if y]
    if len(pts) < 3:
        return None
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    if not sxx:
        return None
    slope = sum((x - mx) * (y - my) for x, y in pts) / sxx
    last = pts[-1][1]
    pred = my + slope * (to_year - mx)
    return {"slope": slope, "pred": pred, "rate": slope / last * 100 if last else None}


def add_forecast(r: dict, hist: list[dict], to_year: int) -> None:
    th = trend([(int(p["year"]), p.get("hourly")) for p in hist], to_year)
    ta = trend([(int(p["year"]), p.get("annual_man")) for p in hist], to_year)
    if th and ta:
        r["fc"] = {"hourly": round(th["pred"]), "pace": round(th["slope"]), "rate": th["rate"],
                   "annual": round(ta["pred"], 1), "annual_pace": round(ta["slope"], 1),
                   "first": hist[0]["year"], "n": len(hist)}


AI_LABEL = {3: "高い", 2: "中", 1: "低い"}


def occ_context(occ: dict | None) -> dict | None:
    """職業別就業者数から、AI時代ページ用の数字を作る。"""
    if not occ or not occ.get("rows"):
        return None
    years = [y for y in occ["years"] if any(r["values"].get(y) for r in occ["rows"])]
    if len(years) < 6:
        return None
    last = years[-1]
    base5 = str(int(last) - 5)
    pre_ai = "2022" if "2022" in years and last > "2022" else None   # 生成AI(ChatGPT)が広まる前の年
    first = years[0]

    def chg(v, a, b):
        x, y = v.get(a), v.get(b)
        if not x or not y:
            return None
        return {"diff": round(x - y, 1), "pct": round((x - y) / y * 100, 1)}

    rows, total = [], None
    has_sub = {r["parent"] for r in occ["rows"] if r["parent"]}
    for r in occ["rows"]:
        v = r["values"]
        if not v.get(last):
            continue
        row = {**r, "now": v[last], "c5": chg(v, last, base5), "cai": chg(v, last, pre_ai) if pre_ai else None,
               "c0": chg(v, last, first), "ai_label": AI_LABEL.get(r["ai"]), "leaf": r["code"] not in has_sub,
               "series": [{"year": y, "v": v.get(y)} for y in years]}
        if r["code"] == "000":
            total = row
        else:
            rows.append(row)
    leaves = [r for r in rows if r["leaf"] and r["c5"]]
    by_level = []
    for lv in (3, 2, 1):
        g = [r for r in leaves if r["ai"] == lv]
        a = sum(r["values"].get(base5, 0) for r in g)
        b = sum(r["now"] for r in g)
        by_level.append({"level": lv, "label": AI_LABEL[lv], "n": len(g), "now": round(b, 0),
                         "diff": round(b - a, 0), "pct": round((b - a) / a * 100, 1) if a else None,
                         "names": [r["short"] for r in g]})
    up = sorted(leaves, key=lambda r: -r["c5"]["pct"])[:5]
    down = sorted(leaves, key=lambda r: r["c5"]["pct"])[:5]
    majors = [r for r in rows if r["major"]]
    mx = max(r["now"] for r in majors) if majors else 1
    return {"table": occ["table"], "last": last, "base5": base5, "pre_ai": pre_ai, "first": first,
            "total": total, "majors": majors, "rows": rows, "by_level": by_level, "up": up, "down": down,
            "max_now": mx, "leaf_rank": sorted(leaves, key=lambda r: -r["c5"]["pct"])}


def ai_scenario(fc: float, h: float, m: float, x: float) -> dict:
    """AIで「高い」仕事の作業が x 割、「中」の仕事が x/2 割置き換わったときの実質時給。
    楽観: 同じ給料で労働時間が減る → 時給が上がる / 悲観: 減った作業のぶん給料も減る → 時給が下がる"""
    r = min(0.9, x * h + x / 2 * m)
    return {"r": round(r * 100, 1), "opt": round(fc / (1 - r)), "pes": round(fc * (1 - r))}


def attach_ai(cfg: dict, data: dict, ranking: list, focus_rank: list) -> dict | None:
    oi = (data.get("occ") or {}).get("by_industry")
    if not oi:
        return None
    by_name = {industries.norm(r["name"]): r for r in oi["rows"]}
    master = industries.by_slug()
    x = cfg.get("ai_scenario_default", 20) / 100
    def put(row, src, approx=False):
        row["ai"] = {"high": src["high"], "mid": src["mid"], "low": src["low"], "src": src["name"], "approx": approx}
        if row.get("fc"):
            row["ai"].update(ai_scenario(row["fc"]["hourly"], src["high"], src["mid"], x))
    for r in ranking:
        src = by_name.get(industries.norm(r["ind"]["name"]))
        if src:
            put(r, src)
    for r in focus_rank:
        f = next((f for f in cfg.get("focus", []) if f["slug"] == r["ind"]["slug"]), {})
        src = by_name.get(industries.norm(f.get("name", "")))
        if src:
            put(r, src)
        elif f.get("occ_name") and by_name.get(industries.norm(f["occ_name"])):
            put(r, by_name[industries.norm(f["occ_name"])], approx=True)
        elif by_name.get(industries.norm(master.get(f.get("parent"), {}).get("name", ""))):
            put(r, by_name[industries.norm(master[f["parent"]]["name"])], approx=True)
    allr = by_name.get(industries.norm("全産業"))
    return {"year": oi["year"], "table": oi["table"], "x": round(x * 100), "all": allr}


def pages(cfg: dict, data: dict) -> list[dict]:
    about = {"path": "about/index.html", "template": "about.html", "title": "このサイトについて・計算方法",
             "changefreq": "monthly"}
    years = data.get("years") or []
    if data.get("status") != "ok" or not years:
        return [{"path": "index.html", "template": "index.html", "noindex": True,
                 "context": {"ready": False}}, {**about, "noindex": True}]

    latest = years[0]
    prev = years[1] if len(years) > 1 else None
    to_year = int(latest["year"]) + 5
    master = industries.by_slug()
    ranking = sorted(
        ({"ind": master[s], **m,
          "diff": (m.get("hourly") - prev["rows"][s]["hourly"])
          if prev and prev["rows"].get(s, {}).get("hourly") and m.get("hourly") else None}
         for s, m in latest["rows"].items() if m.get("hourly") and s in master),
        key=lambda r: -r["hourly"])
    history = {s: [{"year": y["year"], **y["rows"][s]} for y in reversed(years) if s in y["rows"]]
               for s in latest["rows"]}
    for r in ranking:
        add_forecast(r, history[r["ind"]["slug"]], to_year)
    all_hist = [{"year": y["year"], **y["all"]} for y in reversed(years) if y.get("all")]
    all_row = dict(latest["all"])
    add_forecast(all_row, all_hist, to_year)
    sources = [{"year": y["year"], **y["table"]} for y in years]

    # 都道府県
    prefs = data.get("prefs") or []
    pref_rank = []
    if prefs:
        pl, pp = prefs[0], (prefs[1] if len(prefs) > 1 else None)
        for p, m in pl["rows"].items():
            if not m.get("hourly"):
                continue
            hist = [{"year": y["year"], **y["rows"][p]} for y in reversed(prefs) if p in y["rows"]]
            row = {"name": p, "slug": pref_slug(p), **m, "hist": hist,
                   "diff": (m["hourly"] - pp["rows"][p]["hourly"])
                   if pp and pp["rows"].get(p, {}).get("hourly") else None}
            add_forecast(row, hist, to_year)
            pref_rank.append(row)
        pref_rank.sort(key=lambda r: -r["hourly"])

    # 注目の業種(中分類)
    focus_rank = []
    for f in cfg.get("focus", []):
        m = (latest.get("focus") or {}).get(f["slug"])
        if not m or not m.get("hourly"):
            continue
        hist = [{"year": y["year"], **y["focus"][f["slug"]]} for y in reversed(years)
                if (y.get("focus") or {}).get(f["slug"], {}).get("hourly")]
        pv = (prev.get("focus") or {}).get(f["slug"], {}) if prev else {}
        parent = master.get(f["parent"], {})
        row = {"ind": {"slug": f["slug"], "short": f["short"], "name": f["name"], "code": f["code"],
                       "note": f.get("note", ""), "parent": parent}, **m, "hist": hist,
               "diff": m["hourly"] - pv["hourly"] if pv.get("hourly") else None}
        add_forecast(row, hist, to_year)
        focus_rank.append(row)
    focus_rank.sort(key=lambda r: -r["hourly"])

    ai_ctx = attach_ai(cfg, data, ranking, focus_rank)
    fc_rank = sorted((r for r in ranking if r.get("fc")), key=lambda r: -r["fc"]["hourly"])
    fc_focus = sorted((r for r in focus_rank if r.get("fc")), key=lambda r: -r["fc"]["hourly"])
    common = {"latest": latest, "prev": prev, "ranking": ranking, "industries": master,
              "max_hourly": max(r["hourly"] for r in ranking), "ready": True, "all_row": all_row,
              "to_year": to_year, "first_year": years[-1]["year"], "sources": sources,
              "pref_rank": pref_rank, "pref_year": prefs[0]["year"] if prefs else None,
              "pref_map": pref_map(pref_rank) if pref_rank else "",
              "occ": occ_context(data.get("occ")), "ai_ctx": ai_ctx,
              "ai_rank": sorted((r for r in ranking if (r.get("ai") or {}).get("opt")), key=lambda r: -r["ai"]["high"]),
              "ai_focus": sorted((r for r in focus_rank if (r.get("ai") or {}).get("opt")), key=lambda r: -r["ai"]["high"]),
              "fc_rank": fc_rank, "focus_rank": focus_rank, "fc_focus": fc_focus,
              "max_focus": max((r["hourly"] for r in focus_rank), default=1)}

    out = [{"path": "index.html", "template": "index.html",
            "title": f"業界別 実質時給ランキング{latest['year']}｜年収÷労働時間で比べる",
            "context": common}]
    if fc_rank:
        out.append({"path": "yosoku/index.html", "template": "forecast.html",
                    "title": f"{to_year}年の年収・時給予想｜業界別、このペースが続いたら（AI時代の伸び方）",
                    "description": (f"賃金構造基本統計調査の{years[-1]['year']}〜{latest['year']}年の推移から、"
                                    f"業界ごとの{to_year}年の年収・実質時給を直線で延ばして予想。伸びている業界・伸び悩む業界がわかります。"),
                    "context": common})
    oc = common["occ"]
    if oc:
        out.append({"path": "ai/index.html", "template": "ai.html",
                    "title": f"AI時代に増えた仕事・減った仕事｜職業別の働く人の数 {oc['base5']}→{oc['last']}年",
                    "description": (f"総務省「労働力調査」の職業別就業者数で、{oc['base5']}年から{oc['last']}年に増えた職・減った職を一覧に。"
                                    "AIの影響を受けやすい事務職は本当に減っているのか、国際労働機関(ILO)の研究とあわせて確かめます。"),
                    "context": common})
    if pref_rank:
        out.append({"path": "chiiki/index.html", "template": "pref.html",
                    "title": f"都道府県別 実質時給・年収ランキング{prefs[0]['year']}｜地域で比べる",
                    "description": (f"47都道府県の年収と労働時間から実質時給を計算してランキング。"
                                    f"{prefs[0]['year']}年は1位{pref_rank[0]['name']}{pref_rank[0]['hourly']:,.0f}円。"),
                    "context": common})
    for i, r in enumerate(ranking, 1):
        s = r["ind"]["slug"]
        out.append({
            "path": f"gyokai/{s}/index.html", "template": "industry.html",
            "title": f"{r['ind']['short']}業界の実質時給・年収・残業時間（{latest['year']}年）",
            "description": (f"{r['ind']['name']}の実質時給は{r['hourly']:,.0f}円で全{len(ranking)}業界中{i}位。"
                            f"平均年収{r.get('annual_man', 0):,.0f}万円、月の労働時間{r.get('hours_month', 0)}時間。"
                            "賃金構造基本統計調査から計算し、年ごとの推移と今後の予想も掲載。"),
            "context": {**common, "row": r, "rank": i, "hist": history[s]},
        })
    for i, r in enumerate(focus_rank, 1):
        s = r["ind"]["slug"]
        pn = r["ind"]["parent"].get("short", "")
        out.append({
            "path": f"gyoshu/{s}/index.html", "template": "industry.html",
            "title": f"{r['ind']['short']}の年収・実質時給・残業時間（{latest['year']}年）",
            "description": (f"{r['ind']['short']}（{r['ind']['name']}）の実質時給は{r['hourly']:,.0f}円、"
                            f"平均年収{r.get('annual_man', 0):,.0f}万円、月の労働時間{r.get('hours_month', 0)}時間。"
                            f"{pn}業界全体との比較、年ごとの推移と{to_year}年の予想も。"),
            "context": {**common, "row": r, "rank": i, "hist": r["hist"], "is_focus": True,
                        "parent_row": next((x for x in ranking if x["ind"]["slug"] == r["ind"]["parent"].get("slug")), None)},
        })
    out.append({**about, "context": common})
    return out

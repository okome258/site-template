"""業界別 実質時給サイト: e-Stat の返り値の読み取りとページ生成を、作り物のデータで確かめる。"""

import importlib.util
import sys
from pathlib import Path

from core import industries
from core.build import load_site, make_env
from core.estat import parse_stats_data, parse_table_inf

ROOT = Path(__file__).resolve().parent.parent
IND_NAMES = ["Ｃ鉱業，採石業，砂利採取業", "Ｄ建設業", "Ｅ製造業", "Ｆ電気・ガス・熱供給・水道業", "Ｇ情報通信業",
             "Ｈ運輸業，郵便業", "Ｉ卸売業，小売業", "Ｊ金融業，保険業", "Ｋ不動産業，物品賃貸業",
             "Ｌ学術研究，専門・技術サービス業", "Ｍ宿泊業，飲食サービス業", "Ｎ生活関連サービス業，娯楽業",
             "Ｏ教育，学習支援業", "Ｐ医療，福祉", "Ｑ複合サービス事業", "Ｒサービス業（他に分類されないもの）"]
TABS = ["年齢", "勤続年数", "所定内実労働時間数", "超過実労働時間数", "きまって支給する現金給与額",
        "所定内給与額", "年間賞与その他特別給与額", "労働者数"]


def fake_body(year="2025"):
    inds = [("00", "産業計")] + [(f"{i + 1:02d}", n) for i, n in enumerate(IND_NAMES)]
    tabs = [(f"{i + 40}", n) for i, n in enumerate(TABS)]
    cls = [
        {"@id": "tab", "@name": "表章項目", "CLASS": [{"@code": c, "@name": n, "@level": ""} for c, n in tabs]},
        {"@id": "cat01", "@name": "性別", "CLASS": [{"@code": "1", "@name": "男女計", "@level": "1"},
                                                  {"@code": "2", "@name": "男", "@level": "2"}]},
        {"@id": "cat02", "@name": "産業", "CLASS": [{"@code": c, "@name": n, "@level": "1" if c == "00" else "2"}
                                                  for c, n in inds]},
        {"@id": "cat03", "@name": "年齢階級", "CLASS": [{"@code": "01", "@name": "年齢計", "@level": "1"},
                                                    {"@code": "02", "@name": "～19歳", "@level": "2"}]},
        {"@id": "time", "@name": "時間軸", "CLASS": {"@code": f"{year}000000", "@name": f"{year}年", "@level": "1"}},
    ]
    vals = []
    for k, (ic, _) in enumerate(inds):
        base = {"40": 43, "41": 12, "42": 165, "43": 10 + k % 5, "44": 300 + 8 * k, "45": 280 + 7 * k,
                "46": 800 + 30 * k, "47": "X" if k == 3 else 1000}
        for tc, v in base.items():
            for sex in ("1", "2"):
                vals.append({"@tab": tc, "@cat01": sex, "@cat02": ic, "@cat03": "01",
                             "@time": f"{year}000000", "$": str(v if sex == "1" else 999)})
    return {"RESULT": {"STATUS": 0},
            "STATISTICAL_DATA": {"TABLE_INF": {"@id": "0003000001", "TITLE": {"$": "第1表 テスト", "@no": "1"},
                                               "SURVEY_DATE": f"{year}01"},
                                 "CLASS_INF": {"CLASS_OBJ": cls}, "DATA_INF": {"VALUE": vals}}}


def load_fetch():
    cfg, mod = load_site("jikyu")
    return cfg, mod


def test_industry_match():
    for n in IND_NAMES:
        assert industries.match(n), n
    assert industries.match("産業計") is None
    assert industries.match("Ｅ製造業")["slug"] == "manufacturing"
    assert industries.match("サービス業（他に分類されないもの）")["code"] == "R"


def test_parse_and_extract():
    cfg, mod = load_fetch()
    meta = parse_stats_data(fake_body())
    plan = mod.inspect(cfg, meta)
    assert plan and plan["ind_cls"] == "cat02" and plan["item_cls"] == "tab"
    assert plan["fixed"] == {"cat01": "1", "cat03": "01"}
    assert mod.param_name("cat01") == "cdCat01" and mod.param_name("tab") == "cdTab"
    rows = mod.extract(meta["classes"], meta["values"], plan, "2025")
    assert len(rows) == 17 and "_all" in rows
    m = rows["construction"]  # k=2
    annual = 316 * 12 + 860
    assert m["annual_man"] == round(annual / 10, 1)
    assert m["hourly"] == round(annual * 1000 / ((165 + 12) * 12))
    assert m["workers"] == 1000
    assert "workers" not in rows["utilities"] or rows["utilities"]["workers"] != 999  # X は捨てる


def test_single_item_dict():
    assert parse_table_inf({"@id": "1", "TITLE": "t"})["title"] == "t"


def test_pages_render(tmp_path):
    cfg, mod = load_fetch()
    years = []
    for y in ("2025", "2024"):
        meta = parse_stats_data(fake_body(y))
        plan = mod.inspect(cfg, meta)
        rows = mod.extract(meta["classes"], meta["values"], plan, y)
        years.append({"year": y, "table": {"id": "x", "title": "t", "open_date": ""},
                      "all": rows.pop("_all"), "rows": rows})
    env = make_env(cfg)
    for data in ({"status": "ok", "years": years}, {"status": "no_key", "years": []}):
        ps = mod.pages(cfg, data)
        for p in ps:
            page = {"path": p["path"], "title": p.get("title", ""), "description": "", "url": "",
                    "root": "../" * p["path"].count("/") or "./", "noindex": p.get("noindex", False)}
            html = env.get_template(p["template"]).render(site=cfg["site"], cfg=cfg, data=data, page=page,
                                                         built_at="2026-10-10T05:00", **p.get("context", {}))
            assert "e-Stat" in html
    assert len(mod.pages(cfg, {"status": "ok", "years": years})) == 16 + 2

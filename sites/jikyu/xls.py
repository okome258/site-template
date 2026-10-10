"""e-Stat のファイル(Excel)を探して読むための部品。

賃金構造基本統計調査は、2024年分以降が e-Stat のデータベース(API)に入っておらず、
年ごとの Excel だけで公開されている。そのため Excel を e-Stat のファイル一覧API
(getDataCatalog)で探し、ダウンロードして読む。
"""

from __future__ import annotations

import io

import requests


def _list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def catalog(api, stats_code: str, word: str) -> list[dict]:
    """ファイル一覧。1件 = 1つの Excel。"""
    out = []
    pos = 1
    for _ in range(5):  # 100件ずつ最大500件
        body = api._get("getDataCatalog", "GET_DATA_CATALOG", statsCode=stats_code,
                        searchWord=word, limit=100, startPosition=pos)
        cats = _list(body.get("DATA_CATALOG_LIST_INF", {}).get("DATA_CATALOG_INF"))
        for c in cats:
            ds = c.get("DATASET", {})
            dsname = str((ds.get("TITLE") or {}).get("NAME", ""))
            for r in _list((c.get("RESOURCES") or {}).get("RESOURCE")):
                t = r.get("TITLE", {})
                out.append({"id": r.get("@id"), "dataset": dsname, "release": str(r.get("RELEASE_DATE", "")),
                            "no": str(t.get("TABLE_NO", "")), "name": str(t.get("NAME", "")),
                            "url": r.get("URL"), "format": r.get("FORMAT")})
        nxt = (body.get("DATA_CATALOG_LIST_INF", {}).get("RESULT_INF") or {}).get("NEXT_KEY")
        if not nxt:
            break
        pos = int(nxt)
    return out


def download(url: str) -> bytes:
    r = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0 (jikyu.otokuest.com)"})
    r.raise_for_status()
    return r.content


def read_book(blob: bytes) -> dict[str, list[list]]:
    """Excel(xlsx/xls)を {シート名: 行の配列} にする。"""
    if blob[:2] == b"PK":
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
        return {ws.title: [list(row) for row in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
    import xlrd
    wb = xlrd.open_workbook(file_contents=blob)
    return {sh.name: [sh.row_values(i) for i in range(sh.nrows)] for sh in wb.sheets()}


# ---------------- 表を読む ----------------
COLS = ["age", "tenure", "hours_sched", "hours_over", "monthly", "sched_pay", "bonus", "workers"]
# 見出しに必ず入っている言葉(列ずれ検知用)。monthly の次の sched_pay は見出しが下の行にある
CHECK = {"hours_sched": "所定内", "hours_over": "超過", "monthly": "きまって", "bonus": "賞与"}


def _s(v) -> str:
    return "" if v is None else str(v).replace("\n", "").replace(" ", "").replace("　", "")


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_sheet(rows: list[list]) -> dict | None:
    """賃金構造基本統計調査の「第1表」形式のシートから、企業規模計・男女計・全年齢の行を読む。

    返り値: {"labels": {"産業": ..., "民公区分": ..., "都道府県": ...}, "raw": {age: .., ...}}
    形が想定と違えば None(列ずれした値を載せないため)。
    """
    unit_i = c0 = None
    for i, r in enumerate(rows[:40]):
        cells = [_s(c) for c in r]
        if "歳" in cells:
            j = cells.index("歳")
            if cells[j + 1:j + 4] == ["年", "時間", "時間"]:
                unit_i, c0 = i, j
                break
    if unit_i is None:
        return None
    head = {}
    for k, w in CHECK.items():
        col = c0 + COLS.index(k)
        text = "".join(_s(rows[i][col]) for i in range(max(0, unit_i - 3), unit_i) if col < len(rows[i]))
        if w not in text:
            return None
        head[k] = text
    labels = {}
    for r in rows[:unit_i]:
        cells = [_s(c) for c in r]
        for j, c in enumerate(cells[:4]):
            if c in ("産業", "民公区分", "都道府県"):
                rest = [x for x in cells[j + 1:] if x]
                if rest:
                    labels[c] = rest[0]
    for r in rows[unit_i + 1:unit_i + 6]:
        vals = [_num(r[c0 + n]) if c0 + n < len(r) else None for n in range(len(COLS))]
        label = "".join(_s(c) for c in r[:c0])
        if vals[COLS.index("monthly")] is not None and "計" in label:
            return {"labels": labels, "raw": {k: v for k, v in zip(COLS, vals) if v is not None}}
    return None


def zen(n: int) -> str:
    """7 → ７(e-Stat の表題は全角数字)"""
    return str(n).translate(str.maketrans("0123456789", "０１２３４５６７８９"))

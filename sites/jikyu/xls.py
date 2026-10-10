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

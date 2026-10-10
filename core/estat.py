"""政府統計の総合窓口(e-Stat)API の共通クライアント。

キーは環境変数 ESTAT_APP_ID(GitHub Secrets)。https://www.e-stat.go.jp/api/ で無料登録して発行する。
e-Stat のデータを使うページには、利用規約にあるクレジット表記(CREDIT)を必ず載せる。
"""

from __future__ import annotations

import os
import time

import requests

BASE = "https://api.e-stat.go.jp/rest/3.0/app/json"
CREDIT = ("このサービスは、政府統計総合窓口(e-Stat)のAPI機能を使用していますが、"
          "サービスの内容は国によって保証されたものではありません。")


class EStatError(RuntimeError):
    pass


def _list(v) -> list:
    """e-Stat の JSON は1件だと配列でなく dict で返るので、常に list にそろえる。"""
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _text(v) -> str:
    return v.get("$", "") if isinstance(v, dict) else (v or "")


def to_number(s) -> float | None:
    """値を数値に。秘匿(X)・該当なし(-)・「…」などは None。"""
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


class EStat:
    def __init__(self, app_id: str | None = None, interval: float = 0.5):
        self.app_id = app_id if app_id is not None else os.environ.get("ESTAT_APP_ID", "")
        self.interval = interval
        self._last = 0.0
        self.session = requests.Session()

    @property
    def ready(self) -> bool:
        return bool(self.app_id)

    def _get(self, endpoint: str, root: str, **params) -> dict:
        if not self.ready:
            raise EStatError("ESTAT_APP_ID が未設定です")
        wait = self.interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        params = {"appId": self.app_id, "lang": "J", **params}
        for attempt in range(3):
            try:
                r = self.session.get(f"{BASE}/{endpoint}", params=params, timeout=60)
                self._last = time.time()
                r.raise_for_status()
                body = r.json()[root]
                break
            except (requests.RequestException, ValueError, KeyError) as e:
                if attempt == 2:
                    # URL に appId が入るので、例外メッセージはそのまま出さない
                    raise EStatError(f"{endpoint} の取得に失敗: {type(e).__name__}") from None
                time.sleep(3 * (attempt + 1))
        res = body.get("RESULT", {})
        if int(res.get("STATUS", 0)) not in (0, 1):  # 1 = 該当データなし(正常)
            raise EStatError(f"{endpoint}: {res.get('ERROR_MSG', '')}")
        return body

    # ---- 表を探す ----
    def list_tables(self, stats_code: str, search_word: str = "", **params) -> list[dict]:
        body = self._get("getStatsList", "GET_STATS_LIST", statsCode=stats_code,
                         searchWord=search_word, **params)
        return [parse_table_inf(t) for t in _list(body.get("DATALIST_INF", {}).get("TABLE_INF"))]

    # ---- 表の中身 ----
    def get_data(self, stats_data_id: str, **filters) -> dict:
        """filters は cdTab / cdCat01 など e-Stat の絞り込みパラメータ。"""
        body = self._get("getStatsData", "GET_STATS_DATA", statsDataId=stats_data_id,
                         metaGetFlg="Y", cntGetFlg="N", **filters)
        return parse_stats_data(body)


def parse_table_inf(t: dict) -> dict:
    return {
        "id": t.get("@id", ""),
        "stat_name": _text(t.get("STAT_NAME")),
        "title": _text(t.get("TITLE")),
        "survey_date": str(t.get("SURVEY_DATE", "")),
        "open_date": str(t.get("OPEN_DATE", "")),
        "statistics_name": _text(t.get("STATISTICS_NAME")),
    }


def parse_stats_data(body: dict) -> dict:
    """getStatsData の結果を扱いやすい形にする。

    返り値:
        table   : 表の情報(parse_table_inf と同じ形)
        classes : {"cat01": {"name": "産業", "items": {code: name}, "order": [code...]}, ...}
        values  : [{"tab": code, "cat01": code, ..., "time": code, "value": float|None, "unit": str}]
    """
    sd = body.get("STATISTICAL_DATA", {})
    classes = {}
    for obj in _list(sd.get("CLASS_INF", {}).get("CLASS_OBJ")):
        items = _list(obj.get("CLASS"))
        classes[obj["@id"]] = {
            "name": obj.get("@name", ""),
            "items": {c["@code"]: c.get("@name", "") for c in items},
            "level": {c["@code"]: c.get("@level", "") for c in items},
            "order": [c["@code"] for c in items],
        }
    values = []
    for v in _list(sd.get("DATA_INF", {}).get("VALUE")):
        row = {k[1:]: val for k, val in v.items() if k.startswith("@")}
        row["value"] = to_number(v.get("$"))
        values.append(row)
    return {"table": parse_table_inf(sd.get("TABLE_INF", {})), "classes": classes, "values": values}

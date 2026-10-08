"""データ取得とページ一覧。サイトごとに差し替えるのはここと config.yaml / templates だけ。"""

from core.rakuten import Rakuten


def fetch(cfg: dict) -> dict:
    """データを集めて dict で返す。data/latest.json に保存され、テンプレで data として使える。"""
    api = Rakuten(referer=cfg["site"]["rakuten_referer"])
    items = []
    for kw in cfg["keywords"]:
        items += api.search_items(kw, hits=30)
    return {"items": [{"name": i["itemName"], "price": i["itemPrice"],
                       "url": i.get("affiliateUrl") or i["itemUrl"]} for i in items]}


def pages(cfg: dict, data: dict) -> list[dict]:
    """生成するページ。path は public/ からの相対パス。"""
    return [{"path": "index.html", "template": "index.html", "context": {}}]

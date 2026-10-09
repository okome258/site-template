"""楽天ウェブサービス(新API)の共通クライアント。

キーは環境変数から読む(GitHub Secrets):
    RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY / RAKUTEN_AFFILIATE_ID

新APIはサーバー側から呼ぶときも Referer(許可Webサイト)が必須。
許可Webサイトに登録したドメイン(okomen.workers.dev)を Referer/Origin に付ける。
"""

from __future__ import annotations

import os
import time

import requests

ITEM_SEARCH = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/{ver}"
# 新しい順に試す。古い版は新規アプリでは "API Configuration not found" になる
ITEM_SEARCH_VERSIONS = ["20260701", "20260401", "20220601"]
DEFAULT_REFERER = "https://okomen.workers.dev/"


class RakutenError(RuntimeError):
    pass


class Rakuten:
    def __init__(self, referer: str | None = None, interval: float = 1.1):
        self.app_id = os.environ.get("RAKUTEN_APP_ID", "")
        self.access_key = os.environ.get("RAKUTEN_ACCESS_KEY", "")
        self.affiliate_id = os.environ.get("RAKUTEN_AFFILIATE_ID", "")
        self.referer = referer or DEFAULT_REFERER
        self.interval = interval  # 楽天の目安は1秒1回まで
        self._last = 0.0
        self.session = requests.Session()
        self._versions = list(ITEM_SEARCH_VERSIONS)

    @property
    def ready(self) -> bool:
        return bool(self.app_id and self.access_key)

    def _get(self, url: str, params: dict) -> dict:
        if not self.ready:
            raise RakutenError("RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY が未設定です")
        q = {"applicationId": self.app_id, "accessKey": self.access_key,
             "format": "json", "formatVersion": 2, **params}
        if self.affiliate_id:
            q["affiliateId"] = self.affiliate_id
        origin = self.referer.rstrip("/")
        headers = {"Referer": self.referer, "Origin": origin,
                   "User-Agent": "site-template/1.0 (+%s)" % origin}
        for attempt in range(4):
            wait = self.interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            r = self.session.get(url, params=q, headers=headers, timeout=20)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            try:
                body = r.json()
            except ValueError:
                raise RakutenError(f"HTTP {r.status_code}: {r.text[:200]}")
            if r.status_code != 200 or "error" in body or "errors" in body:
                # キーが漏れないよう、エラー本文だけを出す
                raise RakutenError(f"HTTP {r.status_code}: {str(body)[:300]}")
            return body
        raise RakutenError("リトライ上限に達しました")

    def affiliate_link(self, url: str) -> str:
        """任意の楽天ページURLをアフィリエイトリンクにする(IDが無ければそのまま返す)。"""
        if not self.affiliate_id:
            return url
        from urllib.parse import quote
        return f"https://hb.afl.rakuten.co.jp/hgc/{self.affiliate_id}/?pc={quote(url, safe='')}"

    def search_items(self, keyword: str, **params) -> list[dict]:
        """市場商品検索。formatVersion=2 なので Items は商品dictのリスト。"""
        last = None
        for ver in list(self._versions):
            try:
                body = self._get(ITEM_SEARCH.format(ver=ver), {"keyword": keyword, **params})
            except RakutenError as e:
                if "API Configuration not found" in str(e) and len(self._versions) > 1:
                    print(f"::notice::IchibaItem/Search {ver} は使えないので次の版を試します")
                    self._versions.remove(ver)
                    last = e
                    continue
                raise
            return body.get("Items", [])
        raise last or RakutenError("使える版がありません")

"""検索エンジンの登録状況を週1で調べて、レポートを残す + Bing等へ更新を知らせる。

やること
1. Google Search Console API で、各サイトの主要URLのインデックス状況と、直近28日の表示回数・クリック数を取る
   (Secrets の GSC_SA_JSON = サービスアカウントの鍵JSON。未設定ならこの部分は飛ばす)
2. IndexNow で sitemap の全URLを Bing・Yandex 等へ通知する(Googleは非対応)
3. reports/gsc/latest.json と reports/gsc/history.jsonl に結果を書く
4. DISCORD_WEBHOOK_URL があれば要約を送る

使い方: python scripts/search_check.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "gsc"
PROPERTY = "sc-domain:otokuest.com"
INSPECT_MAX = 15  # 1サイトあたりの検査URL数(APIの上限は1プロパティ1日2000件)
JST = dt.timezone(dt.timedelta(hours=9))


def sites() -> list[dict]:
    out = []
    for cfg_path in sorted((ROOT / "sites").glob("*/config.yaml")):
        if cfg_path.parent.name.startswith("_"):
            continue
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        base = (cfg.get("site") or {}).get("base_url")
        if not base:
            continue
        sm = cfg_path.parent / "public" / "sitemap.xml"
        urls = re.findall(r"<loc>([^<]+)</loc>", sm.read_text(encoding="utf-8")) if sm.exists() else []
        out.append({"slug": cfg_path.parent.name, "base": base.rstrip("/"), "urls": urls})
    return out


def indexnow_key() -> str | None:
    for f in (ROOT / "core" / "static").glob("*.txt"):
        if re.fullmatch(r"[0-9a-f]{32}", f.stem) and f.read_text().strip() == f.stem:
            return f.stem
    return None


def indexnow(site: dict, key: str) -> str:
    host = site["base"].split("://", 1)[1]
    try:
        r = requests.post("https://api.indexnow.org/indexnow", timeout=30, json={
            "host": host, "key": key, "keyLocation": f"{site['base']}/{key}.txt", "urlList": site["urls"]})
        return f"HTTP {r.status_code}"
    except requests.RequestException as e:
        return f"失敗: {type(e).__name__}"


def gsc_session():
    raw = os.environ.get("GSC_SA_JSON", "").strip()
    if not raw:
        return None
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    creds = service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
    return AuthorizedSession(creds)


def pick_urls(site: dict) -> list[str]:
    """トップ・独自ページを優先し、残りは週ごとにずらして検査する。"""
    urls = site["urls"]
    first = [u for u in urls if u.rstrip("/") == site["base"] or "/changes/" in u or "/simulator/" in u]
    rest = [u for u in urls if u not in first]
    n = max(0, INSPECT_MAX - len(first))
    if rest and n:
        start = (dt.date.today().isocalendar()[1] * n) % len(rest)
        rest = (rest[start:] + rest[:start])[:n]
    return first + rest[:n]


def inspect(sess, url: str) -> dict:
    r = sess.post("https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
                  json={"inspectionUrl": url, "siteUrl": PROPERTY, "languageCode": "ja"}, timeout=60)
    if r.status_code != 200:
        return {"url": url, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    ir = r.json().get("inspectionResult", {}).get("indexStatusResult", {})
    return {"url": url, "verdict": ir.get("verdict"), "coverage": ir.get("coverageState"),
            "last_crawl": ir.get("lastCrawlTime"), "google_canonical": ir.get("googleCanonical")}


def analytics(sess, site: dict) -> dict:
    end = dt.date.today() - dt.timedelta(days=3)  # 直近2〜3日は未確定なので外す
    start = end - dt.timedelta(days=27)
    host = site["base"].split("://", 1)[1]
    flt = [{"filters": [{"dimension": "page", "operator": "contains", "expression": host}]}]
    url = f"https://www.googleapis.com/webmasters/v3/sites/{requests.utils.quote(PROPERTY, safe='')}/searchAnalytics/query"
    base = {"startDate": str(start), "endDate": str(end), "dimensionFilterGroups": flt}
    tot = sess.post(url, json=base, timeout=60)
    q = sess.post(url, json={**base, "dimensions": ["query"], "rowLimit": 10}, timeout=60)
    if tot.status_code != 200:
        return {"error": f"HTTP {tot.status_code}: {tot.text[:200]}"}
    rows = tot.json().get("rows", [{}])
    t = rows[0] if rows else {}
    return {"from": str(start), "to": str(end), "clicks": t.get("clicks", 0), "impressions": t.get("impressions", 0),
            "position": round(t.get("position", 0), 1),
            "queries": [{"q": r["keys"][0], "clicks": r["clicks"], "impressions": r["impressions"],
                         "position": round(r["position"], 1)} for r in (q.json().get("rows", []) if q.status_code == 200 else [])]}


def sitemaps(sess) -> list[dict]:
    url = f"https://www.googleapis.com/webmasters/v3/sites/{requests.utils.quote(PROPERTY, safe='')}/sitemaps"
    r = sess.get(url, timeout=60)
    if r.status_code != 200:
        return [{"error": f"HTTP {r.status_code}: {r.text[:200]}"}]
    return [{"path": x.get("path"), "last_submitted": x.get("lastSubmitted"), "last_downloaded": x.get("lastDownloaded"),
             "pending": x.get("isPending"), "errors": x.get("errors"), "warnings": x.get("warnings"),
             "submitted": sum(int(c.get("submitted", 0)) for c in x.get("contents", []))}
            for x in r.json().get("sitemap", [])]


def main() -> int:
    now = dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    key = indexnow_key()
    sess = gsc_session()
    report = {"checked_at": now, "gsc": "ok" if sess else "未設定(GSC_SA_JSON なし)", "sites": []}
    if sess:
        report["sitemaps"] = sitemaps(sess)
    for s in sites():
        entry = {"slug": s["slug"], "base": s["base"], "sitemap_urls": len(s["urls"])}
        entry["indexnow"] = indexnow(s, key) if (key and s["urls"]) else "スキップ"
        if sess:
            entry["inspect"] = [inspect(sess, u) for u in pick_urls(s)]
            entry["indexed"] = sum(1 for x in entry["inspect"] if x.get("verdict") == "PASS")
            entry["analytics"] = analytics(sess, s)
        report["sites"].append(entry)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    with (OUT / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"checked_at": now, "sites": [
            {k: e.get(k) for k in ("slug", "indexed")} | {"checked": len(e.get("inspect", [])),
             "clicks": (e.get("analytics") or {}).get("clicks"), "impressions": (e.get("analytics") or {}).get("impressions")}
            for e in report["sites"]]}, ensure_ascii=False) + "\n")

    lines = [f"🔎 検索チェック {now}"]
    for e in report["sites"]:
        if "inspect" in e:
            a = e.get("analytics") or {}
            lines.append(f"・{e['slug']}: 登録 {e['indexed']}/{len(e['inspect'])}ページ(検査分)、"
                         f"28日 表示{a.get('impressions', '?')}回・クリック{a.get('clicks', '?')}回")
        else:
            lines.append(f"・{e['slug']}: Google未確認(GSC未設定)")
        lines.append(f"  IndexNow(Bing等): {e['indexnow']}")
    msg = "\n".join(lines)
    print(msg)
    hook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if hook:
        try:
            requests.post(hook, json={"content": msg[:1900]}, timeout=30)
        except requests.RequestException:
            print("::warning::Discord送信に失敗")
    return 0


if __name__ == "__main__":
    sys.exit(main())

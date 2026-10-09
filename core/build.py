"""サイトを生成する共通ビルダー。

使い方:
    python -m core.build furusato            # データ取得 → HTML生成
    python -m core.build furusato --offline  # 取得せず data/latest.json から生成
    python -m core.build --all               # sites/ 以下の全サイト(_で始まる雛形は除く)

サイト側の決まりごと(sites/<slug>/):
    config.yaml   … サイト名・URL・Analyticsトークンなど + サイト固有の設定
    fetch.py      … fetch(cfg) -> dict でデータを返す / pages(cfg, data) -> list[dict] でページ一覧を返す
    templates/    … サイト固有のテンプレ(core/templates の base.html を継承)
    static/       … サイト固有の画像・CSS(任意)
    public/       … 生成物(Cloudflare が公開するフォルダ。コミットする)
    data/latest.json … 最後に取得できたデータ(取得失敗時はこれで生成する)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import yaml
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"
SITES = ROOT / "sites"
JST = timezone(timedelta(hours=9))

SITE_DEFAULTS = {
    "lang": "ja",
    "description": "",
    "base_url": "",
    "cf_analytics_token": "",
    "google_site_verification": "",  # Search Console の HTMLタグ確認用(content の値だけ)  # Cloudflare Web Analytics のトークン(空なら読み込まない)
    "pr_note": "当サイトはアフィリエイト広告（楽天アフィリエイト等）を利用しています。リンク先での購入・寄付により当サイトに収益が発生する場合があります。",
    "pr_short": "本ページは広告（アフィリエイト）を含みます",  # 全ページ上部の短い表記。詳細はフッターの pr_note
    "operator": "",
    "rakuten_referer": "https://okomen.workers.dev/",
    "theme_color": "#1f6f5c",
    # Google Fonts の family 指定(css2?以降)。例: "family=Zen+Maru+Gothic:wght@700;900"
    "fonts": "family=Zen+Kaku+Gothic+New:wght@400;500;700&family=Zen+Maru+Gothic:wght@700;900",
}


def now_jst() -> datetime:
    return datetime.now(JST)


def load_site(slug: str) -> tuple[dict, object]:
    sdir = SITES / slug
    if not (sdir / "config.yaml").exists():
        raise SystemExit(f"sites/{slug}/config.yaml がありません")
    cfg = yaml.safe_load((sdir / "config.yaml").read_text(encoding="utf-8")) or {}
    cfg["site"] = {**SITE_DEFAULTS, **(cfg.get("site") or {})}
    cfg["site"]["slug"] = slug
    cfg["site"]["base_url"] = cfg["site"]["base_url"].rstrip("/")
    cfg["_dir"] = sdir

    spec = importlib.util.spec_from_file_location(f"sites_{slug}_fetch", sdir / "fetch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(sdir))  # サイト内の補助モジュールを import できるように
    spec.loader.exec_module(mod)
    return cfg, mod


STALE: list[str] = []  # 取得に失敗し前回データで作ったサイト(最後にエラー終了して通知させる)


def get_data(cfg: dict, mod, offline: bool) -> dict:
    """データを取得する。失敗したら前回のデータで続行(サイトを空にしない)。"""
    path = cfg["_dir"] / "data" / "latest.json"
    prev = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if offline:
        if prev is None:
            raise SystemExit("--offline ですが data/latest.json がありません")
        return prev
    try:
        data = mod.fetch(cfg)
        data["fetched_at"] = now_jst().isoformat(timespec="minutes")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return data
    except Exception:
        traceback.print_exc()
        if prev is None:
            raise
        print("::error::データ取得に失敗したため、前回のデータで生成しました(サイトは古いまま表示中)")
        STALE.append(cfg["site"]["slug"])
        return prev


# ---- Jinja2 フィルタ ----
def _yen(v) -> str:
    return f"{int(v):,}円" if v is not None else "-"


def _num(v, digits: int = 0) -> str:
    if v is None:
        return "-"
    return f"{v:,.{digits}f}"


def _date(iso: str, fmt: str = "%Y/%m/%d %H:%M") -> str:
    return datetime.fromisoformat(iso).strftime(fmt) if iso else ""


def make_env(cfg: dict) -> Environment:
    env = Environment(
        loader=ChoiceLoader([FileSystemLoader(str(cfg["_dir"] / "templates")),
                             FileSystemLoader(str(CORE / "templates"))]),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    env.filters.update(yen=_yen, num=_num, jdate=_date)
    return env


def default_pages(cfg: dict, data: dict) -> list[dict]:
    return [{"path": "index.html", "template": "index.html", "context": {}}]


def write_sitemap(out: Path, site: dict, pages: list[dict], lastmod: str) -> None:
    if not site["base_url"]:
        print("::warning::base_url が未設定なので sitemap.xml を出しません")
        return
    rows = []
    for p in pages:
        if p.get("noindex"):
            continue
        loc = site["base_url"] + "/" + p["path"].removesuffix("index.html")
        rows.append(f"  <url><loc>{xml_escape(loc)}</loc><lastmod>{lastmod}</lastmod>"
                    f"<changefreq>{p.get('changefreq', 'daily')}</changefreq></url>")
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(rows) + "\n</urlset>\n", encoding="utf-8")
    (out / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {site['base_url']}/sitemap.xml\n", encoding="utf-8")


def build_site(slug: str, offline: bool = False) -> None:
    cfg, mod = load_site(slug)
    site = cfg["site"]
    data = get_data(cfg, mod, offline)
    pages = (getattr(mod, "pages", None) or default_pages)(cfg, data)
    if not any(p["path"] == "404.html" for p in pages):
        pages.append({"path": "404.html", "template": "404.html", "title": "ページが見つかりません",
                      "noindex": True})
    built = now_jst()

    out = cfg["_dir"] / "public"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for static in (CORE / "static", cfg["_dir"] / "static"):
        if static.exists():
            shutil.copytree(static, out, dirs_exist_ok=True)

    env = make_env(cfg)
    for p in pages:
        path = p["path"]
        depth = path.count("/")
        page = {
            "path": path,
            "title": p.get("title", ""),
            "description": p.get("description", site["description"]),
            "url": site["base_url"] + "/" + path.removesuffix("index.html"),
            # 相対パスで静的ファイルを参照する(404はどの階層でも出るので絶対パス)
            "root": "/" if path == "404.html" else ("../" * depth or "./"),
            "noindex": p.get("noindex", False),
        }
        html = env.get_template(p["template"]).render(
            site=site, cfg=cfg, data=data, page=page, built_at=built.isoformat(timespec="minutes"),
            **p.get("context", {}))
        dest = out / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")

    write_sitemap(out, site, pages, built.strftime("%Y-%m-%d"))
    print(f"[{slug}] {len(pages)}ページを生成 → {out.relative_to(ROOT)}")


def all_sites() -> list[str]:
    return sorted(p.name for p in SITES.iterdir()
                  if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "config.yaml").exists())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--offline", action="store_true", help="取得せず保存済みデータで生成")
    a = ap.parse_args(argv)
    slugs = all_sites() if a.all else a.slugs
    if not slugs:
        ap.error("サイト名か --all を指定してください")
    failed = []
    for s in slugs:
        try:
            build_site(s, offline=a.offline)
        except Exception:
            traceback.print_exc()
            failed.append(s)
    if failed:
        print("失敗したサイト:", ", ".join(failed))
    if STALE:
        print("データが更新できなかったサイト:", ", ".join(STALE))
    # どちらでもエラー終了 → Actions が赤になり GitHub から通知メールが届く(生成物のコミットは先に済む)
    return 1 if (failed or STALE) else 0


if __name__ == "__main__":
    sys.exit(main())

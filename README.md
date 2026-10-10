# site-template

自動更新サイトを量産するための共通テンプレート。Python + Jinja2 で静的HTMLを生成し、GitHub Actions で毎日更新、Cloudflare Workers(workers.dev)で公開する。

| サイト | フォルダ | URL |
|---|---|---|
| ふるさと納税 量コスパ比較 | `sites/furusato` | https://furusato.otokuest.com |
| 業界別 実質時給ランキング(準備中) | `sites/jikyu` | https://jikyu.otokuest.com |

## しくみ

```
core/                 全サイト共通
  build.py            ビルダー(データ取得 → Jinja2 → public/ 書き出し → sitemap.xml/robots.txt)
  rakuten.py          楽天ウェブサービス(新API)クライアント。Referer=okomen.workers.dev を付ける
  qty.py              商品名から内容量を読む(量コスパ系で共通)
  estat.py            e-Stat(政府統計)APIクライアント。キーは ESTAT_APP_ID
  industries.yaml     業界マスタ(産業大分類 C〜R ⇔ slug ⇔ TOPIX-17)。転職・株など業界軸のサイトで共有し、/gyokai/<slug>/ で相互リンクする
  templates/base.html PR表記・OGP・canonical・Cloudflare Web Analytics枠・フッター
  static/             共通CSS・favicon
sites/<slug>/         サイトごとの差分(ここだけ書けば新サイトになる)
  config.yaml         サイト名・URL・Analyticsトークン + サイト固有の設定
  fetch.py            fetch(cfg) でデータ取得 / pages(cfg, data) でページ一覧
  templates/          base.html を継承したページ
  wrangler.jsonc      Cloudflare Workers の設定(public/ を配信)
  public/             生成物(Actionsがコミット → Cloudflareが自動デプロイ)
  data/latest.json    最後に取れたデータ。取得失敗時はこれで生成するのでサイトが空にならない
sites/_skeleton/      新サイトの雛形
```

毎日 5:10 JST に `.github/workflows/build.yml` が全サイトをビルドして `public/` をコミットする。Actions の「Run workflow」からサイト名を指定して手動実行もできる。

## 新しいサイトを作る

```
python scripts/new_site.py <slug> "サイト名"   # sites/_skeleton をコピー
# config.yaml / fetch.py / templates を書き換える
python -m core.build <slug>                   # 楽天キーが必要。--offline で保存済みデータから生成
```

Cloudflare 側(サイトごとに1回):
1. Workers & Pages → 作成 → 「Gitリポジトリをインポート」→ `okome258/site-template`
2. プロジェクト名 = `wrangler.jsonc` の `name`
3. ビルド設定: ルートディレクトリ `sites/<slug>`、ビルドコマンド空、デプロイコマンド `npx wrangler deploy`
4. ビルドの監視パス(Build watch paths)に `sites/<slug>/*` を入れると、他サイトの更新で再デプロイされない
5. 設定 → ドメインとルート → カスタムドメインで `<slug>.otokuest.com` を追加し、`wrangler.jsonc` の `CANONICAL_HOST` と `config.yaml` の `base_url` をそれに合わせる
6. Web Analytics でサイトを追加 → 表示された token を `config.yaml` の `cf_analytics_token` に入れる

## 共通で入っているもの

- 全ページ上部とリンクボタンに「PR」表記(ステマ規制対応)、フッターに広告の説明
- `sitemap.xml` / `robots.txt` / `404.html` / canonical / OGP
- Cloudflare Web Analytics(token を入れたときだけ読み込む)
- 楽天のクレジット表記「Supported by Rakuten Developers」
- 広告リンクは `rel="sponsored noopener"`

## 秘密情報

GitHub Secrets: `RAKUTEN_APP_ID` / `RAKUTEN_ACCESS_KEY` / `RAKUTEN_AFFILIATE_ID` / `ESTAT_APP_ID`(e-Stat。https://www.e-stat.go.jp/api/ で無料登録、アプリケーションIDの「URL」は otokuest.com)。e-Stat を使うページにはクレジット表記(`core/estat.py` の CREDIT)を必ず載せる。コードやファイルには書かない。
屋号ドメインは `otokuest.com`(Cloudflare Registrar)。各サイトはサブドメイン。楽天アプリの許可Webサイトは `okomen.workers.dev`(API の Referer 用。サイトの公開URLとは別でよい)。API 呼び出し時はこれを Referer/Origin に付ける(`site.rakuten_referer` で変更可)。

## ふるさと納税サイトのキャラクター

マスコット「ふくすけ」(ふくら雀。福がふくらむ縁起物)。おこめさん作のイラスト。`sites/furusato/static/fukusuke.webp`(全身・背景透過)、`fukusuke-face.png`(顔・ロゴ用)、`favicon-64.png` / `apple-touch-icon.png`。

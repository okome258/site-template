# site-template

サイト量産用の共通テンプレート。詳しい構成は README.md。

## 守ること
- サイトごとの変更は `sites/<slug>/` の中だけで済ませる。共通部分(`core/`)を変えるときは全サイトを `python -m core.build --all --offline` で確認する。
- 広告リンクには必ず PR 表記と `rel="sponsored noopener"`。PR表記を消さない。
- APIキーはコード・ファイル・ログに出さない(GitHub Secrets のみ)。
- 他サイトの文章・画像を取ってきて載せない。データは公式API(楽天ウェブサービス等)から。
- 架空の口コミ・作り話は載せない。
- `public/` と `data/` は Actions が生成してコミットする。手で編集しない。
- 量の読み取り(`core/qty.py`)を変えたら `python -m pytest -q` を通す。

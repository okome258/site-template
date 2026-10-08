"""新しいサイトを雛形からつくる。

    python scripts/new_site.py <slug> "サイト名"

sites/_skeleton をコピーして __SLUG__ / __NAME__ を置き換える。
あとは config.yaml・fetch.py・templates/ を書き換えるだけ。
"""

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit('使い方: python scripts/new_site.py <slug> "サイト名"')
    slug, name = sys.argv[1], sys.argv[2]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", slug):
        raise SystemExit("slug は英小文字・数字・ハイフン(Workers名とURLになる)")
    dest = ROOT / "sites" / slug
    if dest.exists():
        raise SystemExit(f"{dest} は既にあります")
    shutil.copytree(ROOT / "sites" / "_skeleton", dest)
    for f in dest.rglob("*"):
        if f.is_file():
            t = f.read_text(encoding="utf-8")
            f.write_text(t.replace("__SLUG__", slug).replace("__NAME__", name), encoding="utf-8")
    print(f"作成: sites/{slug}")
    print("次: config.yaml / fetch.py / templates を編集 → python -m core.build", slug)


if __name__ == "__main__":
    main()

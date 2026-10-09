"""日本のタイル地図(都道府県を四角いマスで並べたデフォルメ地図)。

外部素材を使わない自作レイアウトなのでライセンスの心配なし。
- jp_symbol(): ページに1回だけ置く地図の下絵(<symbol id="jp">)
- jp_mini(shop): その都道府県だけ色を付けた小さな地図。shop は「福井県小浜市」のような文字列
"""
from __future__ import annotations

import re

from markupsafe import Markup

T = 10  # 1マスの大きさ
# 都道府県: (列, 行, 幅, 高さ)  列は西→東、行は北→南
TILES: dict[str, tuple[int, int, int, int]] = {
    "北海道": (11, 0, 2, 2),
    "青森県": (11, 2, 2, 1),
    "秋田県": (11, 3, 1, 1), "岩手県": (12, 3, 1, 1),
    "石川県": (8, 4, 1, 1), "富山県": (9, 4, 1, 1), "新潟県": (10, 4, 1, 1), "山形県": (11, 4, 1, 1), "宮城県": (12, 4, 1, 1),
    "島根県": (3, 5, 1, 1), "鳥取県": (4, 5, 1, 1),
    "福井県": (7, 5, 1, 1), "岐阜県": (8, 5, 1, 1), "長野県": (9, 5, 1, 1), "群馬県": (10, 5, 1, 1), "栃木県": (11, 5, 1, 1), "福島県": (12, 5, 1, 1),
    "山口県": (2, 6, 1, 1), "広島県": (3, 6, 1, 1), "岡山県": (4, 6, 1, 1), "兵庫県": (5, 6, 1, 1), "京都府": (6, 6, 1, 1),
    "滋賀県": (7, 6, 1, 1), "愛知県": (8, 6, 1, 1), "山梨県": (9, 6, 1, 1), "埼玉県": (10, 6, 1, 1), "茨城県": (11, 6, 1, 1),
    "福岡県": (1, 7, 1, 1), "香川県": (4, 7, 1, 1), "大阪府": (5, 7, 1, 1), "奈良県": (6, 7, 1, 1), "三重県": (7, 7, 1, 1),
    "静岡県": (9, 7, 1, 1), "東京都": (10, 7, 1, 1), "千葉県": (11, 7, 1, 1),
    "佐賀県": (0, 8, 1, 1), "熊本県": (1, 8, 1, 1), "大分県": (2, 8, 1, 1), "愛媛県": (3, 8, 1, 1), "徳島県": (4, 8, 1, 1),
    "和歌山県": (6, 8, 1, 1), "神奈川県": (10, 8, 1, 1),
    "長崎県": (0, 9, 1, 1), "鹿児島県": (1, 9, 1, 1), "宮崎県": (2, 9, 1, 1), "高知県": (3, 9, 1, 1),
    "沖縄県": (0, 11, 1, 1),
}
W, H = 13 * T, 12 * T
_RE = re.compile(r"^(北海道|東京都|京都府|大阪府|.{2,3}?県)")


def pref_of(shop: str | None) -> str:
    m = _RE.match(shop or "")
    return m.group(1) if m and m.group(1) in TILES else ""


def _rect(x: int, y: int, w: int, h: int, extra: str = "") -> str:
    return f'<rect x="{x*T+1}" y="{y*T+1}" width="{w*T-2}" height="{h*T-2}" rx="2"{extra}/>'


def jp_symbol() -> Markup:
    rects = "".join(_rect(*v) for v in TILES.values())
    return Markup(f'<svg width="0" height="0" style="position:absolute" aria-hidden="true">'
                  f'<symbol id="jp" viewBox="0 0 {W} {H}"><g fill="currentColor">{rects}</g></symbol></svg>')


def jp_mini(shop: str | None) -> Markup:
    p = pref_of(shop)
    if not p:
        return Markup("")
    hit = _rect(*TILES[p], ' class="on"')
    return Markup(f'<svg class="jp-mini" viewBox="0 0 {W} {H}" role="img" aria-label="{p}の位置">'
                  f'<use href="#jp"/>{hit}</svg>')


# URL用のローマ字(TILES と同じ順)
SLUGS = dict(zip(TILES, """hokkaido aomori akita iwate ishikawa toyama niigata yamagata miyagi shimane tottori
fukui gifu nagano gunma tochigi fukushima yamaguchi hiroshima okayama hyogo kyoto shiga aichi yamanashi saitama
ibaraki fukuoka kagawa osaka nara mie shizuoka tokyo chiba saga kumamoto oita ehime tokushima wakayama kanagawa
nagasaki kagoshima miyazaki kochi okinawa""".split()))


def short(pref: str) -> str:
    return pref if pref == "北海道" else pref[:-1]


def level(n: int) -> int:
    """色の濃さ(0〜4)"""
    return 0 if n <= 0 else 1 if n == 1 else 2 if n <= 3 else 3 if n <= 6 else 4


def jp_big(counts: dict[str, int], root: str, current: str = "") -> Markup:
    """都道府県ごとの件数で色分けした、押せる日本地図。件数0の県はリンクなし"""
    parts = []
    for p, (x, y, w, h) in TILES.items():
        n = counts.get(p, 0)
        name = short(p)
        fs = 4.2 if len(name) <= 2 else 3.2
        cls = f"lv{level(n)}" + (" cur" if p == current else "")
        cx, cy = (x + w / 2) * T, (y + h / 2) * T + fs * 0.35
        tile = (f'{_rect(x, y, w, h)}<text x="{cx:g}" y="{cy:g}" font-size="{fs}">{name}</text>')
        if n:
            parts.append(f'<a href="{root}pref/{SLUGS[p]}/" class="{cls}"><title>{p}:{n}件</title>{tile}</a>')
        else:
            parts.append(f'<g class="{cls}">{tile}</g>')
    return Markup(f'<svg class="jp-big" viewBox="0 0 {W} {H}" role="img" aria-label="都道府県の地図">'
                  f'{"".join(parts)}</svg>')

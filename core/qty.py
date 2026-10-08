"""商品名から内容量(総量)を取り出す。「量コスパ」系サイトで共通に使う。

    parse_quantity("【ふるさと納税】豚こま 250g×8パック 計2kg", "weight")  -> (2.0, "計2kg")
    parse_quantity("トイレットペーパー 12ロール×4パック", "count", ["ロール"]) -> (48.0, "12ロール×4")

戻り値の量は weight=kg / volume=L / count=個数。決められないときは (None, 理由)。
読み違えて誤ったランキングを出すより、怪しいものは捨てる方針。
"""

from __future__ import annotations

import re
import unicodedata

WEIGHT = {"kg": 1.0, "キロ": 1.0, "キログラム": 1.0, "g": 0.001, "グラム": 0.001}
VOLUME = {"l": 1.0, "L": 1.0, "リットル": 1.0, "ml": 0.001, "mL": 0.001, "ミリリットル": 0.001, "cc": 0.001}

# 「選べる」系は価格と量が対応しないので捨てる
AMBIGUOUS = re.compile(r"選べる|選択|から選|容量を選|内容量を選|サイズを選|量を選|または")
MULT = re.compile(r"\s*[×xX\*✕]\s*(\d+)\s*(?:袋|パック|P|p|個|本|箱|缶|セット|set|ケース|入り?|尾|枚|玉|束|瓶|缶|食|包|ブロック|切れ|枚入)?")
TOTAL_MARK = re.compile(r"(計|合計|総量|総重量|総内容量|トータル)[\s:：]*$")
NET_MARK = re.compile(r"(正味|正味重量|固形量|解凍後|内容総量)[\s:：約]*$")  # 正味は「計」より優先
_U = r"(?:kg|キロ|g|グラム|l|L|ml|mL|個|本|ロール|袋|箱|パック|P|枚|玉|尾|缶)"
# 「1〜3kg」「1kg〜4kg」「48個/96個」は選択式(寄付額は一番少ない量の値段)。「9〜10月発送」は対象外
RANGE = re.compile(r"\d\s*" + _U + r"\s*[)）]?\s*[〜~～]\s*\d|\d\s*[〜~～]\s*\d+(?:\.\d+)?\s*" + _U)
SLASH = re.compile(r"\d+(?:\.\d+)?\s*(?:kg|キロ|g|l|L|ml|個|本|ロール|袋|箱|パック)\s*[/／]\s*\d+(?:\.\d+)?\s*(?:kg|キロ|g|l|L|ml|個|本|ロール|袋|箱|パック)")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"(?<=\d),(?=\d{3})", "", s)  # 1,000 → 1000
    return s


def _unit_pattern(kind: str, count_units: list[str] | None) -> tuple[re.Pattern, dict]:
    if kind == "weight":
        table = WEIGHT
    elif kind == "volume":
        table = VOLUME
    elif kind == "count":
        table = {u: 1.0 for u in (count_units or [])}
    else:
        raise ValueError(kind)
    units = sorted(table, key=len, reverse=True)  # kg を g より先に試す
    # 2Lサイズ(果物の等級)や 5kgUP などを量と間違えないよう、直後の文字を制限する
    pat = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(" + "|".join(map(re.escape, units)) + r")(?![a-zA-Z]|サイズ|玉|寸|級|以上|前後|程度|UP|up)")
    return pat, table


def parse_quantity(name: str, kind: str, count_units: list[str] | None = None):
    s = _norm(name)
    if AMBIGUOUS.search(s) or RANGE.search(s) or SLASH.search(s):
        return None, "選択式"
    pat, table = _unit_pattern(kind, count_units)

    nets, totals, standalone, multiplied = [], [], set(), set()
    for m in pat.finditer(s):
        v = float(m.group(1)) * table[m.group(2)]
        end, mult, text = m.end(), 1, m.group(0)
        while True:  # 250g×4パック×2 のような連続した掛け算
            mm = MULT.match(s, end)
            if not mm:
                break
            mult *= int(mm.group(1))
            text += f"×{mm.group(1)}"
            end = mm.end()
        q = round(v * mult, 6)
        if q <= 0:
            continue
        before = s[max(0, m.start() - 7):m.start()]
        if (nm := NET_MARK.search(before)):
            nets.append((q, nm.group(1) + text))
        elif (tm := TOTAL_MARK.search(before)):
            totals.append((q, tm.group(1) + text))
        elif mult > 1:
            multiplied.add((q, text))
        else:
            standalone.add((q, text))

    if nets:  # 「正味」があれば最優先(氷やタレを除いた量)
        return min(nets)
    if totals:  # 次に「計」「合計」
        return max(totals)
    sa_vals = {q for q, _ in standalone}
    if len(sa_vals) >= 2:
        # 「1kg 2kg 3kg」のように値が複数並ぶ。掛け算の結果と一致する最大値だけは採用できる
        mu_vals = {q for q, _ in multiplied}
        # 「1kg(500g×2) 1.5kg(500g×3)」のように掛け算も複数なら容量違いの併記なので捨てる
        if len(mu_vals) == 1 and max(sa_vals) in mu_vals:
            return max(standalone | multiplied)
        return None, "量が複数"
    if len({q for q, _ in multiplied}) >= 2:
        return None, "量が複数"  # 「80g×4P・100g×3P」のような容量違いの併記
    cands = standalone | multiplied
    if not cands:
        return None, "量なし"
    return max(cands)

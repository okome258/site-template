"""ふるさと納税 控除上限額(早見表)の計算。simulator.html の JS と同じ値になること(2026-11 に node で全パターン一致を確認)。"""

from sites.furusato.fetch import furusato_limit


def test_known_values_2026():
    assert furusato_limit(5_000_000, 2026) == 58_000
    assert furusato_limit(5_000_000, 2026, spouse=1) == 50_000
    assert furusato_limit(10_000_000, 2026, spouse=1, k2=1) == 155_000


def test_low_income_is_zero():
    assert furusato_limit(1_000_000, 2026) == 0

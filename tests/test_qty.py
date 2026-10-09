import pytest

from core.qty import parse_quantity


@pytest.mark.parametrize("name,kind,units,want", [
    ("【ふるさと納税】令和7年産 コシヒカリ 10kg", "weight", None, 10),
    ("【ふるさと納税】無洗米 5kg×2袋 計10kg", "weight", None, 10),
    ("【ふるさと納税】豚こま切れ 250g×8パック", "weight", None, 2),
    ("【ふるさと納税】豚こま 2kg (250g×8P) 小分け", "weight", None, 2),
    ("【ふるさと納税】牛肉 切り落とし 1,000g", "weight", None, 1),
    ("【ふるさと納税】ハンバーグ 150g×10個×2箱", "weight", None, 3),
    ("【ふるさと納税】ミネラルウォーター 2L×6本×2ケース", "volume", None, 24),
    ("【ふるさと納税】炭酸水 500ml×24本", "volume", None, 12),
    ("【ふるさと納税】トイレットペーパー 12ロール×8パック", "count", ["ロール"], 96),
    ("【ふるさと納税】トイレットペーパー 96ロール(12ロール×8パック)", "count", ["ロール"], 96),
    ("【ふるさと納税】卵 30個 10月～順次発送", "count", ["個", "玉"], 30),
    ("【ふるさと納税】総量1.5kg 鶏もも 300g×5", "weight", None, 1.5),
])
def test_ok(name, kind, units, want):
    q, _ = parse_quantity(name, kind, units)
    assert q == pytest.approx(want)


@pytest.mark.parametrize("name,kind", [
    ("【ふるさと納税】選べる容量 お米 5kg 10kg 20kg", "weight"),
    ("【ふるさと納税】牛肉 1kg 2kg 3kg", "weight"),
    ("【ふるさと納税】シャインマスカット 2房", "weight"),
])
def test_rejected(name, kind):
    q, _ = parse_quantity(name, kind)
    assert q is None


def test_fruit_size_grade_is_not_volume():
    # 2Lサイズは果物の等級なので量ではない
    q, _ = parse_quantity("【ふるさと納税】みかん 2Lサイズ 5kg", "volume")
    assert q is None
    q, _ = parse_quantity("【ふるさと納税】みかん 2Lサイズ 5kg", "weight")
    assert q == pytest.approx(5)


@pytest.mark.parametrize("name", [
    "【ふるさと納税】二本松熟成牛 切り落とし1kg（250g×4袋）〜4kg （250g×16袋）",
    "【ふるさと納税】牛ハラミ 切り落とし 1~2kg 個包装",
    "【ふるさと納税】むきえび 1〜3kg",
    "【ふるさと納税】トイレットペーパー 48個/96個 シングル 240ロール",
])
def test_range_or_choice_rejected(name):
    q, _ = parse_quantity(name, "count" if "ロール" in name else "weight", ["ロール"])
    assert q is None


def test_net_weight_preferred():
    q, ev = parse_quantity("【ふるさと納税】むきえび 2kg（正味重量1.6kg）", "weight")
    assert q == pytest.approx(1.6) and ev.startswith("正味")


def test_evidence_is_clean():
    _, ev = parse_quantity("若鶏 むね肉 約2kg×4袋！計8kg", "weight")
    assert ev == "計8kg"


def test_shipping_month_range_is_ok():
    q, _ = parse_quantity("【ふるさと納税】コシヒカリ 10kg 9～11月発送", "weight")
    assert q == pytest.approx(10)


def test_two_variants_with_multipliers_rejected():
    q, _ = parse_quantity("大型むきエビ1kg(500g×2パック) 1.5kg(500g×3パック)", "weight")
    assert q is None


def test_two_pack_variants_rejected():
    q, _ = parse_quantity("秋鮭いくら醤油漬け 80g × 4P ・ 100g × 3P", "weight")
    assert q is None


def test_same_total_written_two_ways_ok():
    q, _ = parse_quantity("豚こま 250g×8 500g×4 2kg", "weight")
    assert q == pytest.approx(2)


def test_variant_with_times_one_rejected():
    q, _ = parse_quantity("明太子 切れ子 1kg (1kg×1箱） 2kg (1kg×2箱）", "weight")
    assert q is None

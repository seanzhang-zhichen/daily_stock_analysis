from src.services.a_share_index_registry import get_a_share_index, is_a_share_index_code


def test_resolves_common_index_aliases_and_exchange_forms():
    assert get_a_share_index("沪深300").code == "000300"
    assert get_a_share_index("sh000300").name == "沪深300"
    assert get_a_share_index("000300.SZ").code == "000300"
    assert get_a_share_index("csi930955").name == "红利低波100"
    assert get_a_share_index("930955.CSI").code == "930955"


def test_does_not_classify_unknown_or_stock_name_as_index():
    assert get_a_share_index("不存在指数") is None
    assert not is_a_share_index_code("600519")

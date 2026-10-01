"""Reading a Shopee listing.

The network call needs the user's own signed-in session, so what is covered
here is everything around it: link parsing (pure, and the part that silently
sends the wrong request when wrong) and response extraction.
"""
from __future__ import annotations

import pytest

from flowboard.services import shopee


# ── link parsing ─────────────────────────────────────────────────────────

def test_parses_the_share_link_form():
    """`-i.<shopid>.<itemid>` — the shape of a copied share link."""
    assert shopee.parse_link(
        "https://shopee.vn/ao-thun-nam-form-rong-i.123456.7891011"
    ) == ("123456", "7891011")


def test_parses_the_product_path_form():
    assert shopee.parse_link(
        "https://shopee.vn/product/123456/7891011"
    ) == ("123456", "7891011")


def test_shop_id_comes_before_item_id():
    """They are adjacent numbers in the URL and swapping them yields a valid
    request for the wrong product — a silent failure, so pin the order."""
    shop, item = shopee.parse_link("https://shopee.vn/x-i.111.222")
    assert (shop, item) == ("111", "222")
    assert shopee.item_url(shop, item).endswith("itemid=222&shopid=111")


def test_survives_query_strings_and_regional_hosts():
    assert shopee.parse_link(
        "https://shopee.vn/abc-i.5.6?sp_atk=xyz&xptdk=abc"
    ) == ("5", "6")
    assert shopee.parse_link("https://shopee.co.id/abc-i.7.8") == ("7", "8")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://shopee.vn/",
        "https://shopee.vn/search?keyword=ao",
        "not a url",
    ],
)
def test_refuses_links_with_no_product_ids(url):
    with pytest.raises(shopee.ShopeeError):
        shopee.parse_link(url)


def test_refuses_a_non_shopee_host():
    with pytest.raises(shopee.ShopeeError):
        shopee.parse_link("https://tiki.vn/abc-i.1.2")


# ── response extraction ──────────────────────────────────────────────────

def _payload(**item):
    base = {"name": "Áo thun nam", "images": ["hash1", "hash2"], "price": 15000000000}
    base.update(item)
    return {"status": 200, "data": {"data": base}}


def test_extracts_name_and_image_urls():
    out = shopee.extract_product(_payload())
    assert out["name"] == "Áo thun nam"
    assert out["imageUrls"][0] == shopee.IMAGE_CDN + "hash1"
    assert len(out["imageUrls"]) == 2


def test_converts_price_out_of_micro_units():
    """Shopee returns đồng × 100000; showing it raw would read as 15 tỷ."""
    assert shopee.extract_product(_payload())["priceText"] == "150.000đ"


def test_missing_price_is_omitted_not_zero():
    assert shopee.extract_product(_payload(price=0))["priceText"] is None


def test_handles_the_unwrapped_shape_too():
    """The bridge nests under `data` and so does the API; either depth works."""
    out = shopee.extract_product({"data": {"name": "X", "images": ["h"]}})
    assert out["name"] == "X"


def test_a_response_with_no_product_says_to_sign_in():
    """The usual cause is a signed-out session, and a bare 'failed' would send
    the user looking at their link instead."""
    with pytest.raises(shopee.ShopeeError, match="đăng nhập"):
        shopee.extract_product({"status": 200, "data": {"data": None}})


def test_junk_payload_is_rejected():
    with pytest.raises(shopee.ShopeeError):
        shopee.extract_product("not a dict")


def test_non_string_image_hashes_are_dropped():
    out = shopee.extract_product(_payload(images=["ok", None, 5, ""]))
    assert out["imageHashes"] == ["ok"]

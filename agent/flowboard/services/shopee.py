"""Read a Shopee listing through the user's own signed-in session.

The packaged tool opens a logged-in Chrome (``ShopeeBrowserSession``) and calls
Shopee's internal product API — ``/api/v4/item/get?itemid=&shopid=`` — rather
than an affiliate/partner API, and it never carries a partner token. That
detail matters: scraping the public page returns nothing usable because the
listing is rendered in the browser, which is exactly why an Open Graph reader
comes back empty on Shopee while it works elsewhere.

This build reaches the same endpoint through the extension bridge, on the same
cookies, without shipping a second browser.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

ITEM_API = "https://shopee.vn/api/v4/item/get"
# Shopee serves listing photos from this CDN by content hash.
IMAGE_CDN = "https://down-vn.img.susercontent.com/file/"

# Two link shapes are in the wild:
#   .../ten-san-pham-i.<shopid>.<itemid>          (share links, most common)
#   .../product/<shopid>/<itemid>                 (older / app links)
_I_FORM = re.compile(r"-i\.(\d+)\.(\d+)")
_PRODUCT_FORM = re.compile(r"/product/(\d+)/(\d+)")


class ShopeeError(RuntimeError):
    """Message is safe to show a user."""


def parse_link(url: str) -> tuple[str, str]:
    """``(shop_id, item_id)`` from a Shopee product link.

    Both ids are required by the API and neither is guessable, so a link the
    tool cannot read is refused here rather than turned into a request that
    fails for a reason the user cannot act on.
    """
    raw = (url or "").strip()
    if not raw:
        raise ShopeeError("Cần link sản phẩm Shopee.")
    host = (urlparse(raw).hostname or "").lower()
    if host and "shopee." not in host:
        raise ShopeeError("Link này không phải của Shopee.")
    for pattern in (_I_FORM, _PRODUCT_FORM):
        match = pattern.search(raw)
        if match:
            return match.group(1), match.group(2)
    raise ShopeeError(
        "Không đọc được mã sản phẩm trong link. Dùng link đầy đủ dạng "
        "…-i.<shop>.<item> hoặc …/product/<shop>/<item>."
    )


def item_url(shop_id: str, item_id: str) -> str:
    return f"{ITEM_API}?itemid={item_id}&shopid={shop_id}"


def image_url(image_hash: str) -> str:
    return f"{IMAGE_CDN}{image_hash}"


def _first_str(data: dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_product(payload: Any) -> dict[str, Any]:
    """Pull the fields an advert needs out of an ``item/get`` response.

    Shopee wraps the listing in ``data`` and returns prices in micro-units
    (đồng × 100000), which is why they are divided rather than shown raw.
    """
    if not isinstance(payload, dict):
        raise ShopeeError("Shopee trả về dữ liệu không đọc được.")
    # The bridge hands back {status, data}; the API itself nests under `data`
    # too, so unwrap whichever layer is present.
    body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    item = body.get("data") if isinstance(body.get("data"), dict) else body
    if not isinstance(item, dict) or not item.get("name"):
        error = body.get("error_msg") or body.get("error")
        raise ShopeeError(
            f"Shopee không trả về sản phẩm{f': {error}' if error else ''}. "
            "Hãy chắc chắn bạn đã đăng nhập Shopee trong Chrome."
        )

    images = [h for h in (item.get("images") or []) if isinstance(h, str) and h]
    price = item.get("price")
    price_text = None
    if isinstance(price, (int, float)) and price > 0:
        # Micro-units → đồng.
        price_text = f"{int(price / 100000):,}đ".replace(",", ".")

    return {
        "name": _first_str(item, "name") or "",
        "description": _first_str(item, "description"),
        "brand": _first_str(item, "brand"),
        "priceText": price_text,
        "imageHashes": images,
        "imageUrls": [image_url(h) for h in images],
    }


async def fetch_product(url: str) -> dict[str, Any]:
    """Look a listing up through the extension's session-fetch bridge."""
    from flowboard.services.flow_client import flow_client

    shop_id, item_id = parse_link(url)
    resp = await flow_client.trpc_request(
        url=item_url(shop_id, item_id),
        method="GET",
        headers=None,
        body=None,
        timeout=45.0,
    )
    if isinstance(resp, dict) and resp.get("error"):
        raise ShopeeError(
            "Không gọi được Shopee qua extension: "
            f"{str(resp['error'])[:150]}"
        )
    product = extract_product(resp)
    product["shopId"] = shop_id
    product["itemId"] = item_id
    logger.info("shopee: read item %s/%s", shop_id, item_id)
    return product

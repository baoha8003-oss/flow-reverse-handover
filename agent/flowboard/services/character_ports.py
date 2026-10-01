"""Character sockets on a video node, and what they can and cannot carry.

The packaged tool's "component" video mode takes named characters rather than
a start frame: three of them on the Veo lanes, ten on OMNI. Each arrives on
its own socket — `character_1`, `character_2`, … — because which character is
which is the entire point, and a positional list cannot say it.

**What these sockets carry, stated precisely.** Two things, and the difference
is where the id comes from:

* a reference IMAGE — an uploaded still, dispatched as an OMNI ingredient.
  Works with nothing but a picture, and is what every board here used until now.
* a Character ENTITY — `referenceEntities: [{entityId}]`. Flow creates those in
  its own UI, per project; **there is no create-entity API** (mined 18/09:
  `createCharacter` / `/v1/characters` are zero hits, and the packaged tool's
  own refusal tells the user to "tạo lại nhân vật trong đúng project"). So an
  entity arrives here as an id somebody registered in `character_store`, and
  this module reads the LINK to that record — never creates one.

The packaged tool's skill notes warn that Google answers `INVALID_ARGUMENT 13`
to a request holding both entities and images. Unverifiable from here (this
build cannot make an entity to test with), so it is not enforced as a rule —
what IS enforced is the tool's own observable behaviour: an entity the prompt
does not tag is left out of the payload entirely.

Two rules are refusals:

* **A start frame together with character sockets.** Not because of the
  entity rule above — that one is not reachable yet — but because the OMNI
  ingredients dispatch has no start-frame slot at all. Honouring both is
  impossible, so the choice is between refusing and silently dropping the
  start frame the user wired on purpose. Refusing says which two wires
  disagree; dropping produces a clip from the wrong opening image.
* **More characters than the lane allows.** Three on the Veo lanes, ten on
  OMNI, taken from the packaged tool's own configuration table. The ceiling
  is a property of the lane, so the check has to know which lane the node is
  on rather than assuming the larger number.

Pure functions on purpose — no session, no dispatch — so the executor and the
cost estimate can both ask the same questions and get the same answers.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

#: `character_1` … `character_10`, plus the runtime form a character hub
#: produces once it has run and knows the entity's id.
_PORT_RE = re.compile(r"^character[_-](\d{1,2}|[0-9a-f-]{8,})$", re.IGNORECASE)

#: How many characters each lane accepts. OMNI is the wide one; every Veo
#: lane takes three. Absent/unknown lane is treated as a Veo lane — the
#: smaller ceiling, because guessing high is what produces the rejection.
MAX_CHARACTERS = {"omni": 10}
DEFAULT_MAX_CHARACTERS = 3


def is_character_port(port: Optional[str]) -> bool:
    return isinstance(port, str) and bool(_PORT_RE.match(port.strip()))


def port_order(port: Optional[str]) -> tuple[int, str]:
    """Sort key: numbered sockets in numeric order, runtime ones after.

    `character_10` must not sort between `character_1` and `character_2` —
    the order is the order the characters appear in the prompt, and getting
    it wrong swaps who is who in the finished clip.
    """
    if not isinstance(port, str):
        return (9999, "")
    match = _PORT_RE.match(port.strip())
    if match is None:
        return (9999, port)
    body = match.group(1)
    return (int(body), "") if body.isdigit() else (1000, body)


def limit_for(lane: Optional[str]) -> int:
    return MAX_CHARACTERS.get((lane or "").lower(), DEFAULT_MAX_CHARACTERS)


def wired_ports(upstream) -> list[str]:
    """Character sockets that have a wire, whether or not it carries media.

    `collect` drops an empty wire, which is right for building a payload and
    wrong for deciding what the board asked for: an empty result reads the
    same as "no characters wired" and the run then dispatches a plain
    text-to-video. This is how the executor tells those two apart.
    """
    return sorted(
        {
            wire.port
            for wire in upstream
            if isinstance(wire.port, str) and _PORT_RE.match(wire.port)
        }
    )


def collect(upstream) -> list[tuple[str, str]]:
    """``[(port, media_id)]`` for every character wire that has media yet.

    Wires with nothing behind them are skipped rather than reported: a
    character node upstream of a video is filled by the run itself, and
    treating "not yet" as "missing" would refuse the very chains these
    boards are built from. `unfilled` below is what reports those.
    """
    found: list[tuple[str, str]] = []
    for wire in upstream:
        if not is_character_port(wire.port):
            continue
        media = (wire.node.data or {}).get("mediaId")
        if isinstance(media, str) and media.strip():
            found.append((wire.port.strip(), media.strip()))
    found.sort(key=lambda pair: port_order(pair[0]))
    return found


def collect_links(upstream) -> list[tuple[str, str]]:
    """``[(port, character_id)]`` for sockets wired to a REGISTERED character.

    The id is `character_store`'s, not Google's: this module stays pure, so it
    reports the link and lets the caller resolve the record. A node with no
    `characterId` is an ordinary reference image and does not appear here.
    """
    found: list[tuple[str, str]] = []
    for wire in upstream:
        if not is_character_port(wire.port):
            continue
        char_id = (wire.node.data or {}).get("characterId")
        if isinstance(char_id, str) and char_id.strip():
            found.append((wire.port.strip(), char_id.strip()))
    found.sort(key=lambda pair: port_order(pair[0]))
    return found


def unfilled(upstream) -> list[str]:
    """Character sockets wired to something that will never produce media.

    A `character` or `visual_asset` node with no `mediaId` is an upload the
    user never made. A generator upstream is different — it has no media
    before the run and a real one after — so it is not reported.
    """
    out: list[str] = []
    for wire in upstream:
        if not is_character_port(wire.port):
            continue
        if wire.node.type not in ("character", "visual_asset"):
            continue
        data = wire.node.data or {}
        media = data.get("mediaId")
        if isinstance(media, str) and media.strip():
            continue
        link = data.get("characterId")
        if isinstance(link, str) and link.strip():
            # A registered character travels as an entity id, so it needs no
            # uploaded still. Reporting it as "missing media" would refuse the
            # one path that does not use media at all.
            continue
        out.append(wire.port.strip())
    return sorted(set(out), key=port_order)


def validate(
    upstream,
    *,
    lane: Optional[str],
    has_start_frame: bool,
) -> Optional[str]:
    """The reason this node cannot dispatch as a component video, or None.

    Returned as a short code rather than prose because it is stamped onto
    the node and read by the frontend, which renders the explanation.
    """
    wired = [w for w in upstream if is_character_port(w.port)]
    if not wired:
        return None

    if has_start_frame:
        # The OMNI ingredients dispatch has no start-frame slot, so a board
        # asking for both is asking for something that cannot be sent. The
        # alternative to refusing is dropping the start frame silently and
        # generating from the wrong opening image — paid for, and wrong in a
        # way the user cannot see from the result.
        return "character_with_start_frame"

    ceiling = limit_for(lane)
    if len(wired) > ceiling:
        return f"too_many_characters:{len(wired)}>{ceiling}"

    missing = unfilled(upstream)
    if missing:
        return "missing_character:" + ",".join(missing)

    ports = [w.port.strip() for w in wired]
    if len(set(ports)) != len(ports):
        # Two wires into one socket. Which character that socket holds has
        # no answer, and the dispatch would silently keep whichever the
        # graph happened to yield first.
        return "duplicate_character_port"
    return None


def explain(code: str) -> str:
    """The stamped code as something a person can act on."""
    if code == "character_with_start_frame":
        return (
            "Không thể vừa dùng ảnh khung hình đầu vừa dùng cổng nhân vật: "
            "chế độ thành phần không có chỗ cho khung hình đầu. Bỏ một trong "
            "hai đường nối."
        )
    if code.startswith("too_many_characters:"):
        return (
            f"Quá nhiều nhân vật ({code.split(':', 1)[1]}). Lane OMNI nhận tối "
            "đa 10, các lane Veo nhận tối đa 3."
        )
    if code.startswith("missing_character:"):
        ports = code.split(":", 1)[1]
        return f"Chưa chọn ảnh cho nhân vật ở cổng {ports}."
    if code == "duplicate_character_port":
        return "Hai đường nối vào cùng một cổng nhân vật — mỗi cổng một nhân vật."
    return code

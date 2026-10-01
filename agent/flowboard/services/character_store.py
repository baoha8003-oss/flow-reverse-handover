"""The characters a Flow project knows, and the ids Flow gave them.

**There is no create-entity API, and the packaged tool says so in its own
words.** Mined from the binary (18/09): when a character's id does not belong to
the project being generated in, it refuses with

    Nhân vật CHƯA ACTIVE trong Google Flow project hiện tại.
    Hãy tạo lại nhân vật trong đúng project trước khi chạy.

— it tells the user to go make it in Flow. `createCharacter`, `characterEntity`,
`/v1/characters` are all zero hits in the executable. So a "Character Entity" is
created in Flow's own UI, per project, and every tool that uses one merely
*references* it by `entityId`:

    referenceEntities: [{entityId: "…"}]
    referenceImages:   [{mediaId: "…", imageUsageType: IMAGE_USAGE_TYPE_ASSET}]

This module is the registry that makes that reference possible: the name the
prompt tags, the voice, the notes, the uploaded still, and the `entityId` the
user pastes from Flow. It creates nothing at Google and does not pretend to.

**Per project, not per machine.** An entity id is meaningless in another Flow
project — that is what the refusal above is about — so the file is keyed by the
project id and a record carries the project it was made in.

JSON under ``storage/characters/``, the same shape the packaged tool keeps in
its own ``CHARACTERS_DIR``, and the same place this build already keeps personal
templates. No table, no migration: this is small user data that the canvas
references by id.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from flowboard.config import STORAGE_DIR

logger = logging.getLogger(__name__)

#: The voice the packaged tool defaults a new character to (mined: the literal
#: sits next to `voice` in its own character record).
DEFAULT_VOICE = "Achernar"

#: `A-Z0-9_` only. The name is what a prompt tags with `@@Name`, and the tag
#: matcher is a word match — a space or a dash in the name makes the tag
#: unmatchable, which the packaged tool avoids by sanitising here rather than
#: at tag time.
_NAME_STRIP = re.compile(r"[^A-Za-z0-9_]+")


def normalize_name(raw: str) -> str:
    """The name as a taggable token, or "" when nothing is left.

    Vietnamese accents are folded rather than dropped: "Mai Anh" → `MaiAnh`,
    "Bà Trưng" → `BaTrung`. Dropping them would turn two different characters
    into the same empty string.
    """
    import unicodedata

    folded = unicodedata.normalize("NFD", raw or "")
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    folded = folded.replace("đ", "d").replace("Đ", "D")
    # Spaces become nothing, not underscores: `@@MaiAnh` is how the packaged
    # workflows write it.
    folded = re.sub(r"\s+", "", folded)
    return _NAME_STRIP.sub("", folded)[:48]


@dataclass
class Character:
    """One character of one Flow project."""

    name: str
    #: Our own id, stable across renames. The runtime socket is
    #: `character_<id>`; `entity_id` is Google's and may be absent.
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    #: The Flow project this record belongs to. An entity id from another
    #: project is exactly what the packaged tool refuses to send.
    project_id: str = ""
    #: What Flow calls it. Empty until the user pastes it — everything else
    #: here works without it, over the reference-image path.
    entity_id: str = ""
    voice: str = DEFAULT_VOICE
    voice_style: str = ""
    info: str = ""
    #: An uploaded still, as a Flow media id. The second path: a character
    #: with no entity id is still usable as a reference image.
    media_id: str = ""

    def as_json(self) -> dict:
        return asdict(self)

    @property
    def has_entity(self) -> bool:
        return bool(self.entity_id.strip())

    def usable_in(self, project_id: str) -> bool:
        """Whether this record may be SENT to ``project_id``.

        A record with no entity id travels fine — it is an image reference, and
        images are uploaded per project anyway. A record WITH one is bound to
        the project it was created in.
        """
        if not self.has_entity:
            return True
        return bool(project_id) and self.project_id == project_id


def _dir() -> Path:
    return STORAGE_DIR / "characters"


def _path(project_id: str) -> Path:
    # The id comes from Google and is hex-ish, but it arrives over HTTP, so it
    # is not trusted to be a filename.
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", project_id or "unknown")[:64]
    return _dir() / f"{safe}.json"


def list_for(project_id: str) -> list[Character]:
    """Every character of this project, newest last. Missing file → empty."""
    path = _path(project_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        # A corrupt registry must not take the board down with it: the
        # reference-image path still works without any of this.
        logger.warning("character_store: %s unreadable (%s)", path, exc)
        return []
    entries = raw.get("characters") if isinstance(raw, dict) else None
    out: list[Character] = []
    for entry in entries or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        known = {f for f in Character.__dataclass_fields__}
        out.append(Character(**{k: v for k, v in entry.items() if k in known}))
    return out


def get(project_id: str, char_id: str) -> Optional[Character]:
    return next((c for c in list_for(project_id) if c.id == char_id), None)


def _key(name: str) -> str:
    """Two names are the same name when the TAG cannot tell them apart.

    The tag matcher is case-insensitive, so `MaiAnh` and `maianh` are one
    character as far as a prompt is concerned — storing both gives `@@maianh`
    two answers, which is the ambiguity this key exists to catch.
    """
    return normalize_name(name).casefold()


def by_name(project_id: str, name: str) -> Optional[Character]:
    wanted = _key(name)
    return next((c for c in list_for(project_id) if _key(c.name) == wanted), None)


def save(project_id: str, character: Character) -> Character:
    """Insert or replace by id. Returns the stored record.

    The name is normalised on the way in, and the project id is stamped from
    the argument rather than trusted from the body — a record claiming another
    project would be one the run then refuses for no visible reason.
    """
    character.name = normalize_name(character.name)
    if not character.name:
        raise ValueError("tên nhân vật rỗng sau khi chuẩn hoá (chỉ A-Z, 0-9, _)")
    character.project_id = project_id
    others = [c for c in list_for(project_id) if c.id != character.id]
    clash = next((c for c in others if _key(c.name) == _key(character.name)), None)
    if clash is not None:
        # Two characters with one name is a prompt tag with two answers. The
        # packaged tool numbers its sockets for exactly this reason.
        raise ValueError(f"đã có nhân vật tên {character.name!r} trong project này")
    _write(project_id, others + [character])
    return character


def delete(project_id: str, char_id: str) -> bool:
    remaining = [c for c in list_for(project_id) if c.id != char_id]
    if len(remaining) == len(list_for(project_id)):
        return False
    _write(project_id, remaining)
    return True


def _write(project_id: str, characters: list[Character]) -> None:
    path = _path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"projectId": project_id, "characters": [c.as_json() for c in characters]}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    tmp.replace(path)


#: `@@Name` — the tag that decides whether an entity is sent at all.
#:
#: Mined: `⏭ [Chỉ Upload Tag] Bỏ qua Entity NV [<name>] do không được tag tên
#: trong Prompt`. An entity the prompt never names is left out of the payload,
#: because the generator has nothing to attach it to — and sending it anyway
#: spends the character budget of the lane on someone who does not appear.
_TAG_RE_CACHE: dict[str, re.Pattern] = {}


def is_tagged(prompt: str, name: str) -> bool:
    """Whether ``prompt`` tags this character by name.

    Accepts `@@Name` and `@Name`, case-insensitively, and requires a word
    boundary after it so `@@Mai` does not match `@@MaiAnh`.
    """
    token = normalize_name(name)
    if not token or not prompt:
        return False
    pattern = _TAG_RE_CACHE.get(token)
    if pattern is None:
        pattern = re.compile(rf"@{{1,2}}{re.escape(token)}(?![A-Za-z0-9_])", re.I)
        if len(_TAG_RE_CACHE) > 256:
            _TAG_RE_CACHE.clear()
        _TAG_RE_CACHE[token] = pattern
    return bool(pattern.search(prompt))


@dataclass
class EntityPlan:
    """Which registered characters this dispatch may send, and what was left.

    `skipped_untagged` is not an error: it is the packaged tool's own
    behaviour, logged as "⏭ [Chỉ Upload Tag] Bỏ qua Entity NV […] do không
    được tag tên trong Prompt". An entity the prompt never names has nothing to
    attach to, and sending it spends the lane's character budget on someone who
    does not appear.

    `refusal` IS an error, and it is the one the packaged tool spells out: an
    entity id from another project is not active in this one.
    """

    entity_ids: list[str] = field(default_factory=list)
    skipped_untagged: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    refusal: Optional[str] = None


def plan_entities(
    links: list[tuple[str, str]],
    *,
    project_id: str,
    prompt: str,
) -> EntityPlan:
    """Resolve ``[(port, character_id)]`` into entity ids that may be sent.

    Port order is kept and duplicates dropped, the way the packaged tool builds
    the same list (`sorted` by port index, then `seen`/`add`).
    """
    plan = EntityPlan()
    known = {c.id: c for c in list_for(project_id)}
    for port, char_id in links:
        record = known.get(char_id)
        if record is None:
            # Registered in another project, or deleted. Reported rather than
            # ignored: the socket is wired, so the board expects somebody.
            plan.unknown.append(f"{port}:{char_id}")
            continue
        if not record.has_entity:
            # Image-only character. Not an entity, not a problem — the
            # reference-image path carries it.
            continue
        if not record.usable_in(project_id):
            plan.refusal = (
                f"character_wrong_project:{record.name}"
            )
            return plan
        if not is_tagged(prompt, record.name):
            plan.skipped_untagged.append(record.name)
            continue
        if record.entity_id not in plan.entity_ids:
            plan.entity_ids.append(record.entity_id)
    return plan


def explain_refusal(code: str) -> str:
    """The stamped code as the sentence the packaged tool itself uses."""
    if code.startswith("character_wrong_project:"):
        name = code.split(":", 1)[1]
        return (
            f"Nhân vật {name} CHƯA ACTIVE trong Google Flow project hiện tại. "
            f"Tạo lại nhân vật trong đúng project (hoặc bỏ entityId) trước khi chạy."
        )
    if code.startswith("character_unknown:"):
        return (
            "Cổng nhân vật nối tới một nhân vật không còn trong kho của project "
            "này: " + code.split(":", 1)[1]
        )
    return code

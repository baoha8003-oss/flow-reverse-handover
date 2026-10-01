"""The eight Vietnamese script genres the packaged tool writes in.

`data_general/skill/video_skills/README.md` documents a module
``SKILL_generate_script.py`` with ``get_genre_skill(key)`` and
``get_full_skill_prompt(genre_key, use_hook, use_longform, use_3act)``. That
module is compiled into the 353 MB executable and is **not on disk** — only
its README, which specifies each genre precisely enough to rebuild: the
structure, the register, and the per-scene dialogue length.

So these presets are written from that specification rather than extracted
from the binary, which the project owner chose deliberately. Where the skill
tree *does* carry matching material on disk — the four
``cac_the_loai_drama/`` formula files, the McKee structure notes — the preset
names it in ``sources`` and the loader reads the real file at run time
instead of paraphrasing it here.

The three supplements are real files, and they are the ones the README's
``use_hook`` / ``use_longform`` / ``use_3act`` flags switch on.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_SKILLS = "video_skills"
_STRUCTURE = f"{_SKILLS}/05_CAU_TRUC_VA_QUY_TRINH_SAN_XUAT_CHUNG"
_DRAMA = f"{_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/cac_the_loai_drama"

#: The README's three supplementary skills, each a real file on disk.
SUPPLEMENTS: dict[str, str] = {
    "use_hook": f"{_STRUCTURE}/01_CO_MAY_TAO_HOOK_VIRAL_3S/SKILL.md",
    "use_longform": f"{_STRUCTURE}/02_DIEU_PHOI_PHIM_DAI_10_15_30_PHUT/SKILL.md",
    "use_3act": f"{_STRUCTURE}/03_CAU_TRUC_PHAN_CANH_DIEN_ANH_3_HOI/SKILL.md",
}


@dataclass(frozen=True)
class Genre:
    key: str
    title: str
    #: The genre's own instructions, written from the README specification.
    body: str
    #: On-disk files that cover this genre, read at run time. Empty when the
    #: skill tree ships nothing for it — said plainly rather than implied.
    sources: tuple[str, ...] = ()
    #: Words of dialogue per scene, or None where nobody said.
    #:
    #: Load-bearing where it exists: a scene is 5–8 seconds, so 30 words is
    #: the ceiling a voice can deliver without the clip being re-cut. Which is
    #: exactly why it must not be guessed — **the README gives a number for
    #: four of the eight genres**, and a default of 28–30 put that number in
    #: the UI's own label ("28–30 từ/cảnh") for the other four, where it was
    #: this module's invention. Two of those four name on-disk formula files
    #: that specify the shape themselves (50 giây, 6 cảnh); the preset defers
    #: to them instead of overruling them with a rounder number.
    words_per_scene: Optional[tuple[int, int]] = None


GENRES: dict[str, Genre] = {}


def _add(genre: Genre) -> None:
    GENRES[genre.key] = genre


_add(Genre(
    key="lich_su_da_su",
    title="Lịch sử, dã sử & đại chiến sử thi",
    words_per_scene=(30, 32),
    body="""\
Viết kịch bản sử thi Việt Nam theo lối điện ảnh.

- **Faction Visual Bible**: trước cảnh 1, chốt cho MỖI phe một bộ nhận dạng
  riêng — màu áo giáp, kiểu mũ, cờ xí, vũ khí — rồi giữ nguyên xuyên suốt.
  Cảnh giao tranh phải nhìn ra ngay ai thuộc phe nào.
- **Style Anchor**: một trục phong cách duy nhất (điện ảnh 8k, ánh sáng tự
  nhiên, ống kính dài) đặt ở đầu mọi prompt hình, không đổi giữa chừng.
- **Lời thoại 30–32 từ mỗi cảnh**, giọng trang trọng nhưng không sáo.
- **Tên riêng**: lời thoại và thuyết minh ĐƯỢC gọi thẳng tên nhân vật lịch
  sử. Prompt hình thì KHÔNG — mô tả bằng ngoại hình, trang phục, khí chất.""",
))

_add(Genre(
    key="phat_phap_nhan_qua",
    title="Phật pháp & chuyện nhân quả",
    words_per_scene=(28, 30),
    body="""\
Kể chuyện nhân quả theo cấu trúc 5 bước: Duyên Khởi → Gieo Nhân → Trổ Quả →
Khai Thị → An Lạc.

- **100% voiceover**, nhân vật không thoại trực tiếp. Người dẫn kể lại.
- **Lời thoại 28–30 từ mỗi cảnh**, nhịp chậm, câu ngắn, không lên gân.
- Kết ở an lạc chứ không ở trừng phạt: người xem phải thấy lối ra, không chỉ
  thấy hậu quả.
- Hình ảnh: chùa, đường quê, ánh sáng sớm hoặc chiều muộn. Tránh biểu tượng
  tôn giáo của tín ngưỡng khác lẫn vào.""",
))

_add(Genre(
    key="chua_va_kinh_thanh",
    title="Chúa & minh triết Kinh Thánh",
    words_per_scene=(28, 30),
    body="""\
Kể theo lối dụ ngôn Phúc Âm: một tình huống đời thường mang một bài học đức
tin.

- Trục chính là **đức tin và sự tha thứ**, không phải giáo lý hay tranh luận.
- **Lời thoại 28–30 từ mỗi cảnh**, giọng ấm, ngôi thứ nhất hoặc người dẫn.
- Kết bằng một hành động tha thứ cụ thể, không bằng lời khuyên trừu tượng.
- Prompt hình: mô tả vị thế tâm linh bằng trang phục và bối cảnh, không gọi
  tên nhân vật Kinh Thánh.""",
))

_add(Genre(
    key="dao_ly_nhan_sinh",
    title="Đạo lý & triết lý cuộc sống",
    sources=(f"{_DRAMA}/video_dao_ly_bai_hoc_cuoc_song.md",),
    body="""\
Viết theo **The Parable Curve**: đời thường → một nghịch lý làm khựng lại →
thức tỉnh → chữa lành.

- Nghịch lý phải đến từ hành vi của nhân vật, không từ trùng hợp.
- Độ dài mỗi cảnh: theo file công thức bên dưới (README không nêu số).
- Nhân vật "đúng" không được lên lớp nhân vật "sai"; bài học đến từ việc
  người xem tự nhận ra.""",
))

_add(Genre(
    key="kinh_kim_cuong",
    title="Minh triết Kinh Kim Cương & buông xả",
    words_per_scene=(28, 30),
    body="""\
Trục nội dung: **phá chấp tứ tướng** (ngã, nhân, chúng sinh, thọ giả) dẫn tới
buông xả, theo tinh thần Bát Nhã Ba La Mật.

- Mỗi cảnh gỡ đúng MỘT chấp niệm, không gộp.
- **Lời thoại 28–30 từ mỗi cảnh**, ngôn ngữ đời thường — không dùng thuật
  ngữ Hán Việt mà người xem phải tra.
- Không hù doạ, không hứa phước báu. Kết ở sự nhẹ đi.""",
))

_add(Genre(
    key="tich_co_ngu_ngon",
    title="Tích cổ & ngụ ngôn",
    body="""\
**Fabric Wisdom Extraction**: lấy một tích cổ hoặc ngụ ngôn quen thuộc, kể
lại gọn, rồi rút ra bài học áp vào một tình huống hiện đại cụ thể.

- Ba phần rõ rệt: tích cổ → cầu nối → tình huống hôm nay.
- Phần hiện đại phải cụ thể (một nghề, một hoàn cảnh), không nói chung chung.""",
))

_add(Genre(
    key="dao_ly_viral_ngan",
    title="Đạo lý video ngắn (45–60 giây)",
    body="""\
Bản rút gọn của đạo lý nhân sinh, tối ưu cho **lưu và chia sẻ** trên
TikTok/Reels. Tổng 45–60 giây — con số duy nhất README nêu cho thể loại này.

- Hook nằm ở 3 giây đầu và phải là một câu người xem muốn gửi cho người khác.
- Thoại ngắn hơn hẳn bản dài vì tổng thời lượng bị ép; số cảnh và số từ mỗi
  cảnh do người viết chia theo 45–60 giây đó, không phải theo một con số
  module này tự đặt.
- Câu chốt cuối phải đứng một mình được khi tách khỏi video.""",
))

_add(Genre(
    key="drama_lat_keo",
    title="Drama đời sống & lật kèo bất ngờ",
    sources=(
        f"{_DRAMA}/drama_me_chong_nang_dau.md",
        f"{_DRAMA}/drama_cong_so_van_phong.md",
        f"{_DRAMA}/drama_tieu_tam_nguoi_thu_ba.md",
    ),
    body="""\
Drama đời sống với **subtext và cú lật kèo**: điều nhân vật nói không phải
điều họ nghĩ, và cảnh áp chót đảo ngược cách hiểu của người xem.

- Cài manh mối cho cú lật từ sớm, để xem lại lần hai thấy nó vẫn hợp lý.
- Thoại đúng văn nói; độ dài theo file công thức bên dưới (50 giây, 6 cảnh),
  không theo một con số đặt ở đây.
- Tối đa 3 nhân vật xuất hiện cùng lúc trong một cảnh.
- Chỉ người đang nói mở miệng; những người khác khép môi và phản ứng bằng
  cử chỉ.""",
))


def get_genre(key: str) -> Optional[Genre]:
    return GENRES.get(key)


def list_genres() -> list[dict]:
    """For the UI's genre picker."""
    return [
        {
            "key": g.key,
            "title": g.title,
            # None where the README says nothing. The picker used to label
            # every genre "28–30 từ/cảnh" because this defaulted.
            "wordsPerScene": (
                list(g.words_per_scene) if g.words_per_scene else None
            ),
        }
        for g in GENRES.values()
    ]


def full_prompt(
    genre_key: str,
    *,
    use_hook: bool = False,
    use_longform: bool = False,
    use_3act: bool = False,
    budget_bytes: int = 18_000,
) -> str:
    """The packaged tool's ``get_full_skill_prompt``, rebuilt.

    Genre instructions first, then any on-disk material for that genre, then
    the supplements the caller switched on. Reading happens through
    `knowledge`, so a missing asset library degrades to the written body
    rather than raising.
    """
    from flowboard.services import knowledge

    genre = GENRES.get(genre_key)
    if genre is None:
        raise KeyError(
            f"unknown genre {genre_key!r}; known: {', '.join(sorted(GENRES))}"
        )

    parts = [f"# {genre.title}\n\n{genre.body}"]

    wanted = list(genre.sources)
    wanted += [
        SUPPLEMENTS[flag]
        for flag, on in (
            ("use_hook", use_hook),
            ("use_longform", use_longform),
            ("use_3act", use_3act),
        )
        if on
    ]

    if wanted:
        loaded = knowledge.load_paths(wanted, budget_bytes=budget_bytes)
        if loaded.text:
            parts.append(loaded.text)
        if loaded.dropped:
            logger.info(
                "script_genres: %s dropped %s", genre_key, "; ".join(loaded.dropped)
            )
    return "\n\n".join(parts)

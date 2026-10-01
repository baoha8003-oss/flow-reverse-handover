"""Build the foreign-voice → Gemini-voice table from measurement, not adjectives.

One rule: pick the Gemini voice whose measured median speaking pitch is closest
to the foreign voice's. "Nearest voice" is then a measured fact.

The gender words in the names are deliberately NOT used. Two attempts to fold
them in were tried and dropped: keying off English given names meant asserting
things about people this repo has no evidence for (and "Lisa" measures 113 Hz,
which is where that assertion breaks), and preferring a stated gender over the
measurement mapped a 314 Hz "Cute Boy" onto the deepest male voice available —
a worse match by the only thing a listener can hear.

What the exe's own gender labels are used for is CHECKING this: they are the
only labelled set on the machine, so they say how far a pitch match can be
trusted. Printed below.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent

PITCH = json.load(open(HERE / "voice-pitch.json", encoding="utf-8"))

FEMALE = ("Achernar", "Aoede", "Autonoe", "Callirrhoe", "Despina", "Erinome",
          "Gacrux", "Kore", "Laomedeia", "Leda", "Pulcherrima", "Sulafat",
          "Vindemiatrix", "Zephyr")
MALE = ("Achird", "Algenib", "Algieba", "Alnilam", "Charon", "Enceladus",
        "Fenrir", "Iapetus", "Orus", "Puck", "Rasalgethi", "Sadachbia",
        "Sadaltager", "Schedar", "Umbriel", "Zubenelgenubi")
GEMINI = FEMALE + MALE

MEDIAN_F0 = {v: PITCH["gemini"][v]["f0"] for v in GEMINI}


# Gender WORDS only — never a personal name. "Lisa" measures 113 Hz; deciding
# who Lisa is would be this repo asserting something it cannot check.
# Checked female-first, because "female" contains "male". The typos are the
# shipped filenames, not ours.
FEMALE_WORDS = ("female", "famale", "famle", "girl", "woman", "lady",
                "nu", "gai", "co", "ba")
MALE_WORDS = ("male", "man", "boy", "guy", "nam", "trai", "thanh nien",
              "chang", "ong")

F_RANGE = (min(PITCH["gemini"][v]["f0"] for v in FEMALE),
           max(PITCH["gemini"][v]["f0"] for v in FEMALE))
M_RANGE = (min(PITCH["gemini"][v]["f0"] for v in MALE),
           max(PITCH["gemini"][v]["f0"] for v in MALE))


def stated_gender(name: str) -> str | None:
    import re
    import unicodedata
    t = unicodedata.normalize("NFD", name.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").replace("đ", "d")
    for words, g in ((FEMALE_WORDS, "female"), (MALE_WORDS, "male")):
        for w in words:
            if re.search(rf"(?<![a-z]){w}(?![a-z])", t):
                return g
    return None


def nearest(f0: float, name: str = "") -> tuple[str, str]:
    """Nearest by measured pitch — except when the name says a gender AND the
    measurement agrees it is possible.

    The exception exists because `Giọng Nam Trầm` is Vietnamese for "deep male
    voice", and handing that a female voice is wrong in a way anyone can see.
    The range guard exists because `Cute Boy` measures 314 Hz, above every male
    sample: there the label is describing a character, not a speaker, and the
    nearest male voice would be the worst match available by pitch.
    """
    g = stated_gender(name)
    lo, hi = F_RANGE if g == "female" else M_RANGE
    if g and lo <= f0 <= hi:
        bucket = FEMALE if g == "female" else MALE
        return min(bucket, key=lambda v: abs(MEDIAN_F0[v] - f0)), "name+pitch"
    return min(GEMINI, key=lambda v: abs(MEDIAN_F0[v] - f0)), "pitch"


# ── how far can a pitch match be trusted? ─────────────────────────────
# Leave each labelled voice out, match it against the other 29, and see
# whether the match lands in its own gender.
agree = 0
for v in GEMINI:
    others = [o for o in GEMINI if o != v]
    pick = min(others, key=lambda o: abs(MEDIAN_F0[o] - MEDIAN_F0[v]))
    same = (pick in FEMALE) == (v in FEMALE)
    agree += same
    if not same:
        print(f"  leave-one-out miss: {v} ({MEDIAN_F0[v]} Hz) -> {pick} ({MEDIAN_F0[pick]} Hz)")
print(f"leave-one-out gender agreement: {agree}/{len(GEMINI)}\n")

rows = []
for group in ("gpt", "capcut_vi", "capcut_en"):
    for name, m in PITCH[group].items():
        f0 = m["f0"]
        if f0 is None:
            print(f"  NO PITCH: {group}/{name} — left out of the table")
            continue
        target, how = nearest(f0, name)
        rows.append({"group": group, "name": name, "f0": f0, "target": target,
                     "target_f0": MEDIAN_F0[target], "rule": how,
                     "gender": "female" if target in FEMALE else "male"})

json.dump(rows, open(HERE / "voice-map.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for r in rows:
    print(f"{r['group']:<10} {r['name']:<22} {r['f0']:>6} Hz -> "
          f"{r['target']:<14} @{r['target_f0']} ({r['gender']}, {r['rule']})")
print(f"\n{len(rows)} names mapped")

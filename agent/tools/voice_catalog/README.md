# Where `services/voice_catalog.py` comes from

That module is **generated**, not hand-written, because it claims its numbers
are measured and a hand-typed table cannot be checked. Run these three in order
to re-derive it:

1. `python measure_voice_pitch.py` — decodes every voice sample the packaged
   tool ships (30 Gemini + 9 ChatGPT + 58 CapCut) and writes the median
   speaking pitch of each to `voice-pitch.json`. Needs `ffmpeg` and the tool
   installed at `D:/TOOL_VIDEO/TOOL`.
2. `python build_voice_map.py` — turns those numbers into
   `voice-map.json`: each foreign name → the Gemini voice nearest in pitch.
   Prints a leave-one-out check first, which is the honest measure of how far
   a pitch match can be trusted (**25/30** land in the right gender; all five
   misses sit between 146 and 169 Hz, where the two groups overlap).
3. `python gen_voice_catalog.py` — emits
   `../../flowboard/services/voice_catalog.py`.

`voice-descriptions.json` is the exe's own `GOOGLE_VOICE_GROUPS`, mined verbatim
(3 groups: no-voice, 14 female, 16 male). It is checked in rather than re-mined
each run because parsing Nuitka's marshalled constants is brittle, and the
mining is a one-off against a fixed vendor voice set. The command that produced
it is in the plan file; `tests/test_voice_names.py` locks its shape against
`tts.VOICES` so a bad re-mine fails rather than ships.

Nothing here runs at app start. The generated module is plain data.

"""Editing policies shared by short-clip selection and rendering."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EditStyle:
    label: str
    selection: str = ""
    preferred_seconds: tuple[int, int] = (45, 90)
    framing: str = "auto"
    steady: bool = False
    pause_trigger: float = 0
    pause_keep: float = 0
    max_trim_ratio: float = 0
    caption_words: int = 7
    caption_chars: int = 34
    caption_seconds: float = 3
    caption_scale: float = 1
    caption_margin: float = .14
    accent: str = "&H0000D7FF"
    audio_filter: str = ""


STYLES = {
    "balanced": EditStyle("Balanced"),
    "fast": EditStyle(
        "Fast Cuts", preferred_seconds=(18, 45),
        selection="Prefer a hook in the first two seconds and a payoff that arrives quickly. Avoid long setup. "
                  "Choose a complete reveal, reaction, contrast, or punchline that survives a tight edit.",
        pause_trigger=.85, pause_keep=.38, max_trim_ratio=.25, caption_words=4,
        caption_chars=25, caption_seconds=1.8, caption_scale=1.1, caption_margin=.16,
        audio_filter="highpass=f=75,acompressor=threshold=0.10:ratio=2:attack=12:release=140,"
                     "loudnorm=I=-16:LRA=7:TP=-1.5",
    ),
    "podcast": EditStyle(
        "Podcast", preferred_seconds=(35, 100), steady=True,
        selection="Look for an answer, personal story, debate, or clear exchange. Include the question or enough of "
                  "the preceding sentence to explain who or what is being discussed. Preserve a meaningful reaction.",
        pause_trigger=1.65, pause_keep=.85, max_trim_ratio=.15, caption_words=9,
        caption_chars=42, caption_seconds=3.5, caption_scale=.9, accent="&H00D3EBFF",
        audio_filter="highpass=f=72,acompressor=threshold=0.075:ratio=2.5:attack=15:release=180,"
                     "loudnorm=I=-16:LRA=8:TP=-1.5",
    ),
    "gameplay": EditStyle(
        "Gameplay", preferred_seconds=(18, 48), framing="stream",
        caption_words=6, caption_chars=30, caption_seconds=2.3, caption_scale=.93,
        caption_margin=.15, accent="&H00A0F7BD", audio_filter="loudnorm=I=-17:LRA=11:TP=-1.5",
    ),
    "tutorial": EditStyle(
        "Tutorial", preferred_seconds=(28, 85), framing="fit",
        selection="Choose one complete, reproducible demonstration. Keep its starting state, exact action or setting, "
                  "and visible or spoken result together. Prefer a useful instruction over a dramatic line. "
                  "Avoid clips that start after a required command or finish before its outcome.",
        caption_words=8, caption_chars=40, caption_seconds=3.4, caption_scale=.87,
        caption_margin=.13, accent="&H00F4E7A8", audio_filter="highpass=f=62,loudnorm=I=-17:LRA=9:TP=-1.5",
    ),
}

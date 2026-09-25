"""Deterministic formatting and timing checks for synthetic captions."""

from dataclasses import dataclass

MAX_LINES = 2
MAX_LINE_LENGTH = 42


@dataclass(frozen=True)
class CaptionCue:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class CaptionEvidence:
    rule: str
    cue_index: int | None
    observed: str
    limit: str


@dataclass(frozen=True)
class CaptionCheck:
    evidence: tuple[CaptionEvidence, ...]

    @property
    def valid(self) -> bool:
        return not self.evidence


class UnsafeCaptions(ValueError):
    def __init__(self, evidence: tuple[CaptionEvidence, ...]) -> None:
        super().__init__("captions fail deterministic quality checks")
        self.evidence = evidence


def _cue_evidence(
    cue: CaptionCue, index: int, previous_end_ms: int | None
) -> tuple[CaptionEvidence, ...]:
    evidence: list[CaptionEvidence] = []
    if cue.start_ms < 0 or cue.end_ms <= cue.start_ms:
        evidence.append(
            CaptionEvidence(
                "invalid_timing",
                index,
                f"{cue.start_ms}..{cue.end_ms} ms",
                "0 <= start_ms < end_ms",
            )
        )
    if previous_end_ms is not None and cue.start_ms < previous_end_ms:
        evidence.append(
            CaptionEvidence(
                "overlapping_cues",
                index,
                f"starts at {cue.start_ms} ms",
                f"start_ms >= {previous_end_ms} ms",
            )
        )
    return tuple(evidence) + _text_evidence(cue, index)


def _text_evidence(cue: CaptionCue, index: int) -> tuple[CaptionEvidence, ...]:
    evidence: list[CaptionEvidence] = []
    if not cue.text.strip():
        evidence.append(
            CaptionEvidence("empty_text", index, repr(cue.text), "nonblank")
        )
    lines = cue.text.splitlines()
    if len(lines) > MAX_LINES:
        evidence.append(
            CaptionEvidence("too_many_lines", index, str(len(lines)), str(MAX_LINES))
        )
    for line in lines:
        if len(line) > MAX_LINE_LENGTH:
            evidence.append(
                CaptionEvidence(
                    "line_too_long", index, str(len(line)), str(MAX_LINE_LENGTH)
                )
            )
    return tuple(evidence)


def validate_captions(cues: tuple[CaptionCue, ...]) -> CaptionCheck:
    """Return evidence for each violated synthetic caption rule."""

    if not cues:
        return CaptionCheck((CaptionEvidence("missing_cues", None, "0", "at least 1"),))
    evidence: list[CaptionEvidence] = []
    previous_end_ms: int | None = None
    for index, cue in enumerate(cues):
        evidence.extend(_cue_evidence(cue, index, previous_end_ms))
        previous_end_ms = cue.end_ms
    return CaptionCheck(tuple(evidence))

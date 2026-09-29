"""Demo-grade notation checks for synthetic provider-facing text."""

import re
from dataclasses import dataclass
from enum import StrEnum

DEMO_NOTICE = (
    "Demo-grade notation check with synthetic provider profiles. "
    "It does not synthesize speech and does not verify pronunciation, meaning, "
    "or provider behavior. "
    "It may flag harmless notation or miss pronunciation errors. A passing "
    "result only means this limited rule set found no notation issue."
)


class Notation(StrEnum):
    TEMPERATURE = "temperature"
    PERCENT = "percent"
    KILOMETER = "kilometer"
    KILOGRAM = "kilogram"
    POUND = "pound"


@dataclass(frozen=True)
class SpokenTextCapabilities:
    provider: str
    model: str
    supported_notation: frozenset[Notation]

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("provider and model must be named")
        if any(not isinstance(item, Notation) for item in self.supported_notation):
            raise ValueError("supported notation must use Notation values")


@dataclass(frozen=True)
class SpokenTextIssue:
    code: str
    token: str
    explanation: str


@dataclass(frozen=True)
class SpokenTextResult:
    authored_text: str
    candidate_text: str
    spoken_text: str
    capabilities: SpokenTextCapabilities
    issues: tuple[SpokenTextIssue, ...]

    @property
    def notation_compatible(self) -> bool:
        """Whether this limited notation rule set found an issue."""

        return not self.issues

    @property
    def demo_notice(self) -> str:
        return DEMO_NOTICE


@dataclass(frozen=True)
class TtsInputVersion:
    id: str
    script_version_id: str
    authored_text: str
    candidate_text: str
    spoken_text: str
    capabilities: SpokenTextCapabilities


class UnsafeSpokenText(ValueError):
    def __init__(self, issues: tuple[SpokenTextIssue, ...]) -> None:
        super().__init__("spoken text contains unsafe or ambiguous notation")
        self.issues = issues


_NUMBER = r"-?\d+(?:\.\d+)?"
_TEMPERATURE = re.compile(rf"(?<!\w)(?P<number>{_NUMBER})\s*°(?P<unit>[FC])\b")
_PERCENT = re.compile(rf"(?<!\w)(?P<number>{_NUMBER})\s*%(?!\w)")
_UNIT = re.compile(rf"(?<!\w)(?P<number>{_NUMBER})\s*(?P<unit>km|kg|lb)\b")
_AMBIGUOUS_TEMPERATURE = re.compile(rf"(?<!\w){_NUMBER}\s*\*[FC]\b")
_AMBIGUOUS_UNIT = re.compile(rf"(?<!\w){_NUMBER}\s*m\b")
_UNSUPPORTED_UNIT = re.compile(rf"(?<!\w){_NUMBER}\s*(?:mph|ft|oz|ml|cm|F|C)\b")
_ATTACHED_UNIT = re.compile(rf"(?<!\w){_NUMBER}(?P<unit>[A-Za-z]+)\b")
_SYMBOL = re.compile(r"[*&$@#/+°%]")
_UNIT_WORDS = {
    "km": (Notation.KILOMETER, "kilometer"),
    "kg": (Notation.KILOGRAM, "kilogram"),
    "lb": (Notation.POUND, "pound"),
}


def _quantity_word(number: str, singular: str) -> str:
    return singular if float(number) == 1 else f"{singular}s"


def _normalize_temperature(
    match: re.Match[str], capabilities: SpokenTextCapabilities
) -> str:
    if Notation.TEMPERATURE in capabilities.supported_notation:
        return match.group()
    unit = "Fahrenheit" if match.group("unit") == "F" else "Celsius"
    quantity = _quantity_word(match.group("number"), "degree")
    return f"{match.group('number')} {quantity} {unit}"


def _normalize_percent(
    match: re.Match[str], capabilities: SpokenTextCapabilities
) -> str:
    if Notation.PERCENT in capabilities.supported_notation:
        return match.group()
    return f"{match.group('number')} percent"


def _normalize_unit(match: re.Match[str], capabilities: SpokenTextCapabilities) -> str:
    notation, singular = _UNIT_WORDS[match.group("unit")]
    if notation in capabilities.supported_notation:
        return match.group()
    quantity = _quantity_word(match.group("number"), singular)
    return f"{match.group('number')} {quantity}"


def _unit_issues(candidate_text: str) -> tuple[SpokenTextIssue, ...]:
    issues: list[SpokenTextIssue] = []
    for pattern, code, explanation in (
        (_AMBIGUOUS_UNIT, "ambiguous_unit", "Specify what m means."),
        (_UNSUPPORTED_UNIT, "unsupported_unit", "Spell out or verify this unit."),
    ):
        for match in pattern.finditer(candidate_text):
            issues.append(SpokenTextIssue(code, match.group(), explanation))
    for match in _ATTACHED_UNIT.finditer(candidate_text):
        if match.group("unit") not in {"km", "kg", "lb", "m", "F", "C"} and not any(
            issue.token == match.group() for issue in issues
        ):
            issues.append(
                SpokenTextIssue(
                    "unsupported_unit",
                    match.group(),
                    "Spell out or verify this attached unit.",
                )
            )
    return tuple(issues)


def _symbol_issues(candidate_text: str) -> tuple[SpokenTextIssue, ...]:
    recognized = [
        *_TEMPERATURE.finditer(candidate_text),
        *_PERCENT.finditer(candidate_text),
        *_AMBIGUOUS_TEMPERATURE.finditer(candidate_text),
    ]
    issues: list[SpokenTextIssue] = []
    for match in _SYMBOL.finditer(candidate_text):
        if not any(item.start() <= match.start() < item.end() for item in recognized):
            issues.append(
                SpokenTextIssue(
                    "unsupported_symbol",
                    match.group(),
                    "This symbol has no declared safe pronunciation.",
                )
            )
    return tuple(issues)


def _notation_issues(candidate_text: str) -> tuple[SpokenTextIssue, ...]:
    temperatures = tuple(
        SpokenTextIssue(
            "ambiguous_temperature",
            match.group(),
            "A star is not a verified degree sign; supply explicit spoken text.",
        )
        for match in _AMBIGUOUS_TEMPERATURE.finditer(candidate_text)
    )
    return temperatures + _unit_issues(candidate_text) + _symbol_issues(candidate_text)


def prepare_spoken_text(
    authored_text: str,
    capabilities: SpokenTextCapabilities,
    *,
    candidate_text: str | None = None,
) -> SpokenTextResult:
    """Normalize declared notation; do not infer actual speech quality."""

    if not authored_text.strip():
        raise ValueError("authored text must not be blank")
    candidate = authored_text if candidate_text is None else candidate_text
    if not candidate.strip():
        raise ValueError("candidate spoken text must not be blank")
    issues = _notation_issues(candidate)
    spoken = _TEMPERATURE.sub(
        lambda match: _normalize_temperature(match, capabilities), candidate
    )
    spoken = _PERCENT.sub(lambda match: _normalize_percent(match, capabilities), spoken)
    spoken = _UNIT.sub(lambda match: _normalize_unit(match, capabilities), spoken)
    return SpokenTextResult(authored_text, candidate, spoken, capabilities, issues)

# Spoken-text gate

The authored script and provider-facing TTS input are separate versions.
`WorkflowRepository.create_script_version` stores exact authored text, and the
TTS creation path reads that immutable script version. `prepare_spoken_text`
takes the authored text, an explicit synthetic
provider/model capability profile, and an optional corrected candidate. It
returns the original text, the candidate, a normalized spoken form, and issues.
The original text is never rewritten. A candidate with issues cannot become a
TTS input version through `WorkflowRepository.create_tts_input_version`.

| Synthetic input | Literal model result | Reason |
| --- | --- | --- |
| `450°F` | `450 degrees Fahrenheit` | Explicit temperature notation |
| `50%` | `50 percent` | Unambiguous percent notation |
| `5 km`, `2 kg`, `1 lb` | Spelled-out units | Explicit supported units |
| `450*F`, `450F`, `5m`, `R&D` | Blocked with issue codes and tokens | Ambiguous or undeclared pronunciation |

A profile names the provider and model and declares which of the recognized
temperature, percent, and unit forms that model may receive unchanged. The
synthetic profiles in tests make no claim about real providers. Selected unsafe
symbols, attached unit abbreviations, and listed common spaced abbreviations
are reported instead of guessed. An explicit corrected
candidate can be stored while retaining the authored text and exact script ID;
the deterministic gate validates pronunciation notation, not semantic
equivalence or creative intent. A person still approves any subsequent repair
before a video job is submitted.

`TTS_INPUT` can only be created by the validated path. Its artifact dependency
points to the source script version; its record also keeps the authored text,
candidate, normalized spoken text, and capability profile for inspection.
The run checks that its selected script and TTS input match. After a script
revision, `bind_tts_input` must attach a validated version derived from the new
script before approval can proceed.

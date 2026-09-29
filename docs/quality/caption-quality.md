# Synthetic caption quality gate

`validate_captions` checks timed `CaptionCue` values and returns structured
`CaptionEvidence` records. Each record names a rule, the zero-based cue index,
the observed value, and the limit. The gate currently enforces:

| Rule | Synthetic threshold | Example evidence |
| --- | --- | --- |
| Timing | `0 <= start_ms < end_ms` | Cue 1 has `900..900 ms` |
| Overlap | Each cue starts at or after the prior cue ends | Cue 1 starts at 900 ms after a cue ending at 1000 ms |
| Text | Nonblank, at most two lines | Cue 0 contains three lines |
| Line length | At most 42 characters per line | Cue 0 has a 64-character line |
| Presence | At least one cue | No cues supplied |

The threshold values are demo policy, not claims about a particular platform's
caption standards. These checks concern caption structure only; a bad
pronunciation is a TTS/video issue, and transcript accuracy needs a separate
evaluation method.

A `CAPTION_FORMAT` run names an existing exact video version. Human approval
moves the run to `ready`; `record_caption_repair` accepts corrected cues only
when they pass this gate. It creates a new caption version with that video as
its source and leaves the video untouched. The old caption version remains
readable. The video provider cannot be used for this action.

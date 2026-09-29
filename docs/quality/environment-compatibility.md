# Script and avatar environment fixture

The synthetic pre-render gate compares versioned scene metadata. A script
version stores its authored text, a required environment, and an evidence
phrase that must appear in that text. An avatar version stores its environment.

| Script fixture | Avatar fixture | Result |
| --- | --- | --- |
| `script-oven`: “Bake the bread in the oven.”, kitchen, phrase `oven` | `avatar-office`: office | Grounded `environment_mismatch` finding |
| Same script | `avatar-kitchen`: kitchen | Compatible |
| Neutral script | Any declared avatar environment | Compatible |

The mismatch evidence names the exact script and avatar version IDs, the
phrase, the required environment, and the avatar environment. The finding
enters `needs_input`; the human chooses whether to revise the script or change
the avatar. Approval stays blocked until the selected replacement resolves the
mismatch, including a new TTS input when the script changes. No video provider
job is launched for an unresolved mismatch.

These values are declared synthetic metadata. The gate does not inspect an
avatar image or infer a scene from arbitrary text. A wrong metadata label could
still pass; raw-media or model evaluation would need separate evidence.

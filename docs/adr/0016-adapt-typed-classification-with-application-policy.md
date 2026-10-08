# 0016: Adapt typed classification with application policy

Status: Accepted

The current feedback contract combines classification, explanation, evidence
references, and proposed repair scope. Jev's Decisions API returns typed choices
and probabilities without generating explanations. It cannot implement this
contract by replacing the chat model name alone.

Keep Jev optional behind `ModelProvider`. Ask it to select a failure class and
evaluate supplied facts against each candidate. Pure application code validates
the typed results, gates confidence and evidence support, and constructs a
clearly attributed summary or diagnostic clarification. Derive repair scope
from the existing deterministic policy, then apply the same strict validator.
Preserve human branch selection and approval requirements.

Generating invented model reasoning from probabilities was rejected because
Jev does not provide it. Letting Jev choose actions was rejected because repair
authority belongs to application policy. Switching the default based on fifteen
synthetic cases was rejected because that evidence is too narrow.

The matched run supports further evaluation on cost and latency, but leaves
the confidence/support thresholds uncalibrated. Jev's alpha transport adds
provider-specific schema maintenance; pinned release selection and shared
bounded HTTP transport constrain that dependency. Full results and replay
instructions are in [the comparison note](https://github.com/woolnerd/media-qc-agent/blob/1762b8a69dcd694d6203d7b18b107aae6568be21/docs/agent/jev-classification.md).

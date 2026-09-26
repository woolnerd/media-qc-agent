# Agent interpretation boundary

`model.ModelProvider` accepts review feedback, exact artifact version IDs, and
available evidence. It returns raw structured-output text. Model names, API
credentials, prompts and transport belong to an adapter, outside this contract.
The interface grants no persistence, approval or media-provider access.

`FakeModelProvider` copies an exact request-to-response fixture mapping. Repeated
requests return the same output; unknown feedback, versions or evidence fail
explicitly. Tests and synthetic scenarios can also supply malformed output to
exercise the validation boundary. This fake performs no language understanding
and makes no claims about live model quality. A live adapter remains future work.

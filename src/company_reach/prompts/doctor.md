---
version: 1
---
The marker for this request is: $marker

Return that marker, exactly as written above, in the `marker` field.

This is a start-up probe. It checks three things at once: that the endpoint
answers, that it honours a JSON schema, and that the beginning of the prompt
reached the model — if the marker comes back wrong, the context window may be
smaller than the prompts this tool sends.

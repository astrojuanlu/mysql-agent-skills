# pi-agent-json

Typed Pydantic models of the JSON event stream that `pi --mode json`
emits: lifecycle events (`session`, `agent_start`, turns), messages with
content blocks (text / thinking / toolCall), tool executions with their
results, provider retries, and the per-message usage object with its
nested cost breakdown.

`parse_pi_event(raw)` converts one stream object into a typed event; an
unmodeled event type or content shape raises `PiEventParseError` — new
event types are instrument debt to fix here, never stream data to drop
silently.

The models are parsing types only: they describe pi's output and are
never serialized under a format schema of their own.

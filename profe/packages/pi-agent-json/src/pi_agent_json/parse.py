"""Parsing pi stream objects into the typed event vocabulary.

Thin layer over `models.PiEvent`: one function (`parse_pi_event`), one
error type (`PiEventParseError`). Kept apart from the models so the
event vocabulary and the parse policy can evolve independently.
"""

import typing as t

from pydantic import TypeAdapter, ValidationError

from .models import PiEvent


class PiEventParseError(ValueError):
    """A pi stream object does not fit the modeled event vocabulary."""


_PI_EVENT_ADAPTER = TypeAdapter(PiEvent)


def parse_pi_event(raw: dict[str, t.Any]) -> PiEvent:
    """Parse one pi stream object into a typed event.

    Raises `PiEventParseError` for unmodeled event types or content
    shapes — stream data is never silently dropped (a silently-ignored
    event is exactly the evidence-loss class the protocol exists to
    prevent).
    """
    try:
        return _PI_EVENT_ADAPTER.validate_python(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(part) for part in first["loc"]) or "<root>"
        raise PiEventParseError(
            f"unmodeled pi event (type={raw.get('type')!r}): {loc}: {first['msg']}"
        ) from exc

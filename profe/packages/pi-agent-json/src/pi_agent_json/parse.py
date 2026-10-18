"""Parsing pi stream objects into the typed event vocabulary.

Thin layer over the models: `parse_pi_event` for the `--mode json`
stream, `parse_session_entry` for pi session files, plus the two error
types. Kept apart from the models so the vocabularies and the parse
policies can evolve independently.
"""

import typing as t

from pydantic import TypeAdapter, ValidationError

from .models import PiEvent, SessionEntry


class PiEventParseError(ValueError):
    """A pi stream object does not fit the modeled event vocabulary."""


class PiSessionParseError(ValueError):
    """A pi session-file entry does not fit the modeled vocabulary."""


_PI_EVENT_ADAPTER: TypeAdapter[PiEvent] = TypeAdapter(PiEvent)
_SESSION_ENTRY_ADAPTER: TypeAdapter[SessionEntry] = TypeAdapter(SessionEntry)


def parse_pi_event(raw: dict[str, t.Any]) -> PiEvent:
    """Parse one pi stream object into a typed event.

    Raises `PiEventParseError` for unmodeled event types or content
    shapes — stream data is never silently dropped (a silently-ignored
    event is exactly the evidence-loss class the protocol exists to
    prevent).
    """
    return _parse(raw, _PI_EVENT_ADAPTER, PiEventParseError, "pi event")


def parse_session_entry(raw: dict[str, t.Any]) -> SessionEntry:
    """Parse one pi session-file line into a typed entry.

    Same strictness as `parse_pi_event`: unmodeled entry types raise
    `PiSessionParseError`.
    """
    return _parse(raw, _SESSION_ENTRY_ADAPTER, PiSessionParseError, "pi session entry")


def _parse[T](
    raw: dict[str, t.Any],
    adapter: TypeAdapter[T],
    error_type: type[ValueError],
    what: str,
) -> T:
    try:
        return adapter.validate_python(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(part) for part in first["loc"]) or "<root>"
        raise error_type(
            f"unmodeled {what} (type={raw.get('type')!r}): {loc}: {first['msg']}"
        ) from exc

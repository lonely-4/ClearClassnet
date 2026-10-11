"""Errors plugins can hit while inspecting a flow."""


class BodyUnavailable(Exception):
    """The message body is not available for inspection or rewriting.

    Raised when the body exceeded the configured buffer limit and was spooled
    for passthrough only.
    """

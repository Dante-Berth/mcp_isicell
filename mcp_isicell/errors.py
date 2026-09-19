"""Errors the tool layer turns into messages an LLM can act on."""

from __future__ import annotations


class IsicellError(Exception):
    """Base for everything this server raises deliberately."""


class NotConfigured(IsicellError):
    """No usable ISiCell checkout. Offline authoring still works."""


class ValidationRefused(IsicellError):
    """A build was refused because the model has error-level findings."""


class ConfirmationRequired(IsicellError):
    """A destructive action needs confirm=True."""


class BuildFailed(IsicellError):
    """Codegen, cmake or make exited non-zero."""

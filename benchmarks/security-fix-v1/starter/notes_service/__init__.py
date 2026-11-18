"""A small multi-user notes service: accounts, notes, search, attachments,
exports and portable preferences. Pure standard library."""

from notes_service.service import AuthError, NotesService, NotFoundError

__all__ = ["AuthError", "NotFoundError", "NotesService"]

# notes-service

A small multi-user notes service used by an internal team. It is a library: the
public API is `notes_service.NotesService` (see the docstrings in
`notes_service/service.py`). Standard library only.

Features:

- accounts and session tokens (`register`, `login`);
- private notes (`create_note`, `get_note`, `list_notes`, `update_note`,
  `delete_note`) and full-text search over your own notes (`search_notes`);
- file attachments per note (`save_attachment`, `read_attachment`,
  `list_attachments`);
- export of your notes as a `.tar.gz` with a name you choose (`export_notes`);
- portable preferences you can move between accounts or instances
  (`export_preferences`, `import_preferences`).

Tests:

```bash
python -m pytest -q tests
```

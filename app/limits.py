"""Shared attachment limits; HTTP multipart framing needs a little extra room."""
MEBIBYTE = 1024 * 1024
MAX_ATTACHMENT_BYTES = 200 * MEBIBYTE
MAX_ATTACHMENT_PAGES = 500
MAX_REQUEST_BYTES = MAX_ATTACHMENT_BYTES + 5 * MEBIBYTE

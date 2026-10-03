"""Upload validation (spec 31).

The governing rule is that nothing the client tells us about a file is
trusted: not the filename, not the Content-Type. Both are attacker-controlled.
Only the bytes are evidence.
"""

import hashlib
import os
import re
import unicodedata

from django.conf import settings
from rest_framework import serializers

# Leading bytes that identify a format. Chosen over python-magic to avoid a
# native libmagic dependency for what is a small, fixed allow-list.
MAGIC_SIGNATURES = [
    (b"\xff\xd8\xff", "image/jpeg", {"jpg", "jpeg"}),
    (b"\x89PNG\r\n\x1a\n", "image/png", {"png"}),
    (b"GIF87a", "image/gif", {"gif"}),
    (b"GIF89a", "image/gif", {"gif"}),
    (b"%PDF-", "application/pdf", {"pdf"}),
    (b"PK\x03\x04", "application/zip", {"zip", "xlsx", "docx", "pptx"}),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/vnd.ms-office", {"xls", "doc", "ppt"}),
]

# Types safe to render inline. Everything else downloads as an attachment.
# SVG and HTML are deliberately absent: rendering either inline on our own
# origin is stored XSS.
INLINE_SAFE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf"}

TEXT_EXTENSIONS = {"txt", "csv", "md", "log"}
VIDEO_EXTENSIONS = {"mp4", "webm"}

EXTENSION_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword", "csv": "text/csv", "txt": "text/plain",
    "zip": "application/zip", "mp4": "video/mp4", "webm": "video/webm",
}


def sanitize_filename(name):
    """Reduce a user-supplied name to something safe to store and display.

    The result is used for display and Content-Disposition only -- never to
    build a path on disk, which uses a generated UUID name instead.
    """
    name = os.path.basename(name or "")
    name = name.replace("\x00", "")
    name = unicodedata.normalize("NFKD", name)
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    name = re.sub(r"\.{2,}", ".", name).strip(". ")
    return (name or "file")[:200]


def get_extension(filename):
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    return ext[:10]


def sniff_mime(header_bytes, extension):
    """Identify content from its leading bytes.

    Returns None when nothing matches, which for a binary extension is a
    rejection: a .png whose bytes are not PNG is either corrupt or a
    deliberate disguise.
    """
    for signature, mime, extensions in MAGIC_SIGNATURES:
        if header_bytes.startswith(signature):
            # ZIP-based office formats share PK\x03\x04; trust the extension
            # within that family rather than reporting everything as zip.
            if mime == "application/zip" and extension in EXTENSION_MIME:
                return EXTENSION_MIME[extension]
            return mime
    return None


def validate_upload(uploaded_file):
    """Run every spec 31 check. Returns (extension, mime, sha256)."""
    max_bytes = settings.ATTACHMENT_MAX_SIZE_MB * 1024 * 1024
    if uploaded_file.size > max_bytes:
        raise serializers.ValidationError({
            "file": [f"File exceeds the {settings.ATTACHMENT_MAX_SIZE_MB} MB limit."]
        })
    if uploaded_file.size == 0:
        raise serializers.ValidationError({"file": ["The file is empty."]})

    extension = get_extension(uploaded_file.name)
    if extension not in settings.ATTACHMENT_ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(settings.ATTACHMENT_ALLOWED_EXTENSIONS))
        raise serializers.ValidationError({
            "file": [f"Files of type '.{extension}' are not allowed. Allowed: {allowed}."]
        })

    uploaded_file.seek(0)
    header = uploaded_file.read(2048)
    uploaded_file.seek(0)

    sniffed = sniff_mime(header, extension)

    if extension in TEXT_EXTENSIONS:
        # Text has no magic bytes; verify it decodes and holds no NUL.
        try:
            header.decode("utf-8")
        except UnicodeDecodeError:
            raise serializers.ValidationError({
                "file": ["This file does not appear to be valid text."]
            })
        if b"\x00" in header:
            raise serializers.ValidationError({
                "file": ["This file does not appear to be valid text."]
            })
        mime = EXTENSION_MIME.get(extension, "text/plain")
    elif extension in VIDEO_EXTENSIONS:
        mime = EXTENSION_MIME.get(extension, "application/octet-stream")
    elif sniffed is None:
        raise serializers.ValidationError({
            "file": [f"The file contents do not match a .{extension} file."]
        })
    else:
        expected_family = {
            e for sig, m, exts in MAGIC_SIGNATURES if m == sniffed for e in exts
        }
        if expected_family and extension not in expected_family:
            raise serializers.ValidationError({
                "file": [f"The file contents do not match the .{extension} extension."]
            })
        mime = sniffed

    # Images get a structural check; a file can carry a valid header and still
    # be a malformed or polyglot payload.
    if extension in {"jpg", "jpeg", "png", "gif", "webp"}:
        try:
            from PIL import Image

            uploaded_file.seek(0)
            Image.open(uploaded_file).verify()
            uploaded_file.seek(0)
        except Exception:
            raise serializers.ValidationError({"file": ["This image file is not valid."]})

    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)

    return extension, mime, digest.hexdigest()

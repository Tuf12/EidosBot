"""Desktop chat attachments: stage files, save them into the inbox, and view photos.

The web app already uploads into ``~/EidosWorkspace/inbox`` and tells the agent
the saved path. The desktop composer uses the same inbox so a pasted photo or
a chosen file is a normal file the agent can open. Photos are also sent through
the vision model on the turn they arrive.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024
MAX_ATTACHMENTS = 8
MAX_VIEWED_IMAGES = 4
STAGING_DIR = Path.home() / ".eidos" / "attach-staging"
IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}


def is_image_path(path: str) -> bool:
    """True when the filename looks like a still image the vision model can open."""
    return Path(path or "").suffix.lower() in IMAGE_SUFFIXES


def is_image_record(record: Dict[str, Any]) -> bool:
    """True when a shared-file record is a photo."""
    mime = str(record.get("mime") or "")
    if mime.startswith("image/") and mime != "image/svg+xml":
        return True
    return is_image_path(str(record.get("name") or record.get("path") or ""))


def pasted_image_name() -> str:
    """Display name for a photo that came from the clipboard."""
    return time.strftime("Pasted image %H-%M-%S.png")


def staging_path(suffix: str = ".png") -> Path:
    """Empty path under the staging folder. The caller writes the bytes."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    return STAGING_DIR / f"{uuid.uuid4().hex}{suffix}"


def pending_from_path(
    path: str,
    *,
    staged: bool = False,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate a local file and return the composer payload for it."""
    resolved = Path(os.path.expanduser(path or "")).resolve()
    label = name or resolved.name or "file"
    if resolved.is_dir():
        raise ValueError(f"{label} is a folder. Attach a file.")
    if not resolved.is_file():
        raise ValueError(f"File not found: {label}")
    if not os.access(resolved, os.R_OK):
        raise ValueError(f"File is not readable: {label}")
    size = resolved.stat().st_size
    if size <= 0:
        raise ValueError(f"{label} is empty")
    if size > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"{label} is larger than 32 MB")
    return {
        "id": uuid.uuid4().hex,
        "name": name or resolved.name,
        "path": str(resolved),
        "staged": staged,
    }


def discard_staged(items: List[Dict[str, Any]]) -> None:
    """Delete clipboard staging files. Leave files the user picked on disk."""
    for item in items:
        if not item.get("staged"):
            continue
        path = str(item.get("path") or "")
        if not path:
            continue
        try:
            os.remove(path)
        except OSError:
            pass


def save_chat_attachments(
    registry: Any,
    items: List[Dict[str, Any]],
    conversation_id: str = "",
) -> List[Dict[str, Any]]:
    """Copy composer attachments into the inbox and register them."""
    saved: List[Dict[str, Any]] = []
    staged: List[Dict[str, Any]] = []
    for item in items:
        path = str(item.get("path") or "")
        name = str(item.get("name") or Path(path).name or "file")
        data = Path(path).read_bytes()
        if not data:
            raise ValueError(f"{name} is empty")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"{name} is larger than 32 MB")
        saved.append(registry.save_upload(
            name,
            data,
            conversation_id=conversation_id,
            note="Attached from the desktop chat",
        ))
        if item.get("staged"):
            staged.append(item)
    discard_staged(staged)
    return saved


def attached_image_paths(text: str, limit: int = 4) -> List[str]:
    """Image paths listed as ``- name: /absolute/path`` in an attachment prompt."""
    found: List[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        body = stripped[2:]
        mark = body.rfind(": /")
        if mark < 0:
            continue
        path = body[mark + 2:].strip()
        if is_image_path(path) and os.path.isfile(path):
            found.append(path)
        if len(found) >= limit:
            break
    return found


def image_file_to_jpeg_base64(path: str) -> str:
    """Resize a local image and return JPEG base64 for the vision model."""
    from PIL import Image, ImageOps

    from eidos.perception.screen import ScreenCapture

    with Image.open(path) as raw:
        image = ImageOps.exif_transpose(raw)
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.thumbnail((1280, 1280))
        return ScreenCapture.to_base64(image, format="JPEG", quality=80)


def observe_attached_images(
    router: Any,
    records: List[Dict[str, Any]],
    user_text: str,
) -> str:
    """Look at attached photos and return notes the agent can reason from."""
    images = [rec for rec in records if is_image_record(rec)]
    if not images:
        return ""
    if router is None or not hasattr(router, "vision_chat"):
        names = ", ".join(str(rec.get("name") or "image") for rec in images)
        return (
            "Vision notes: the photos were saved, but no vision model is available "
            f"to look at them ({names})."
        )

    viewed = images[:MAX_VIEWED_IMAGES]
    skipped = images[MAX_VIEWED_IMAGES:]
    question = (user_text or "Describe this image in detail.").strip()[:1000]
    notes: List[str] = []
    for rec in viewed:
        name = str(rec.get("name") or "image")
        path = str(rec.get("path") or "")
        try:
            image_b64 = image_file_to_jpeg_base64(path)
            response = router.vision_chat(
                prompt=(
                    "The user attached this image in the desktop chat. "
                    "Describe what is visible so you can answer them: objects, people, "
                    "readable text, layout, colors, and any data shown in the image. "
                    f"User message: {question}"
                ),
                image_base64=image_b64,
            )
            text = (getattr(response, "content", "") or "").strip()
            if not text or text.startswith("Error communicating"):
                detail = text or "The vision model returned no description."
                notes.append(f"{name}: could not view this image. {detail}")
            else:
                notes.append(f"{name}:\n{text[:1500]}")
        except Exception as exc:
            logger.warning("Could not view attached image %s: %s", path, exc)
            notes.append(f"{name}: could not view this image ({exc}).")

    lines = [
        "What you can see in the attached photos:",
        "These notes are your view of the photos. The file paths above are the originals.",
        "",
        "\n\n".join(notes),
    ]
    if skipped:
        extra = ", ".join(str(rec.get("name") or "image") for rec in skipped)
        lines.append("")
        lines.append(f"Additional photos were saved but not opened in this turn: {extra}.")
    return "\n".join(lines).strip()

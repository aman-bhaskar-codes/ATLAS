import os
import shutil
import uuid

from fastapi import APIRouter, File, UploadFile

from atlas.interfaces.api.schemas import AttachmentRef

router = APIRouter(tags=["attachments"])

ATTACHMENT_DIR = os.path.expanduser("~/.atlas/attachments")
os.makedirs(ATTACHMENT_DIR, exist_ok=True)


@router.post("/attachments", response_model=AttachmentRef)
async def upload_attachment(file: UploadFile = File(...)) -> AttachmentRef:
    """Upload an attachment and return its reference ID.

    The file is stored securely on the local filesystem and its type is inferred.
    """
    att_id = f"att_{uuid.uuid4().hex[:12]}"

    content_type = file.content_type or "application/octet-stream"
    filename = file.filename or "unknown"

    if content_type.startswith("image/"):
        att_type = "image"
    elif content_type == "application/pdf":
        att_type = "pdf"
    elif content_type in ["text/plain", "text/markdown", "application/json"] or filename.endswith(
        (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".cpp", ".md")
    ):
        att_type = "markdown" if filename.endswith(".md") else "code"
    else:
        att_type = "file"

    file_path = os.path.join(ATTACHMENT_DIR, f"{att_id}_{filename}")

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return AttachmentRef(id=att_id, type=att_type)

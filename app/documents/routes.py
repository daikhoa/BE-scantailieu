import asyncio
import re
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from ..auth import current_user
from ..database import connect
from ..object_storage import SupabaseStorage
from . import storage
from .models import Manifest, PageUpdate, RenameDocument
from .page_update import save_page_update

router = APIRouter(prefix="/api/v1/documents", tags=["Documents"])


@router.get("")
def list_documents(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0, le=1000),
    user: dict = Depends(current_user),
):
    with connect(request.app.state.settings) as db:
        total = db.execute(
            "SELECT count(*) AS total FROM documents WHERE user_id = ?", (user["id"],)
        ).fetchone()["total"]
        rows = db.execute(
            "SELECT * FROM documents WHERE user_id = ? ORDER BY updated_at DESC, id LIMIT ? OFFSET ?",
            (user["id"], limit, offset),
        ).fetchall()
    return {
        "items": [storage.summary(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def save_request(
    request: Request, user: dict, identifier: str | None = None, partial: bool = False
):
    if identifier:
        await run_in_threadpool(
            storage.document_row, request.app.state.settings, user["id"], identifier
        )
    if not request.headers.get("content-type", "").startswith("multipart/form-data;"):
        raise HTTPException(415, "Cần gửi multipart/form-data.")
    slots = request.app.state.upload_slots
    try:
        await asyncio.wait_for(slots.acquire(), timeout=2)
    except TimeoutError:
        raise HTTPException(
            429,
            "Máy chủ đang lưu các tài liệu khác. Vui lòng thử lại sau.",
            headers={"Retry-After": "3"},
        ) from None
    try:
        return await parse_and_save(request, user, identifier, partial)
    finally:
        slots.release()


async def parse_and_save(
    request: Request, user: dict, identifier: str | None, partial: bool = False
):
    create_key = request.headers.get("idempotency-key") if identifier is None else None
    if create_key and not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", create_key):
        raise HTTPException(422, "Mã yêu cầu lưu không hợp lệ.")
    async with request.form(max_files=101, max_fields=1, max_part_size=256 * 1024) as form:
        raw = form.get("manifest")
        if not isinstance(raw, str):
            raise HTTPException(422, "Thiếu thông tin tài liệu.")
        try:
            manifest = (PageUpdate if partial else Manifest).model_validate_json(raw)
        except ValidationError:
            raise HTTPException(
                422, "Thông tin trang hoặc công thức chỉnh sửa không hợp lệ."
            ) from None
        if partial:
            return await run_in_threadpool(
                save_page_update, request.app.state.settings, user["id"], identifier, manifest, form
            )
        return await run_in_threadpool(
            storage.save_document,
            request.app.state.settings,
            user["id"],
            manifest,
            form,
            identifier,
            create_key,
        )


@router.post("", status_code=201)
async def create_document(request: Request, user: dict = Depends(current_user)):
    return await save_request(request, user)


@router.put("/{document_id}")
async def update_document(document_id: UUID, request: Request, user: dict = Depends(current_user)):
    return await save_request(request, user, document_id.hex)


@router.get("/{document_id}")
def get_document(document_id: UUID, request: Request, user: dict = Depends(current_user)):
    settings = request.app.state.settings
    return storage.detail(settings, storage.document_row(settings, user["id"], document_id.hex))


@router.put("/{document_id}/pages")
async def update_pages(document_id: UUID, request: Request, user: dict = Depends(current_user)):
    return await save_request(request, user, document_id.hex, partial=True)


@router.patch("/{document_id}")
def rename_document(
    document_id: UUID, body: RenameDocument, request: Request, user: dict = Depends(current_user)
):
    settings = request.app.state.settings
    storage.document_row(settings, user["id"], document_id.hex)
    with connect(settings) as db:
        changed = db.execute(
            "UPDATE documents SET name = ?, revision = revision + 1, updated_at = ? WHERE id = ? AND user_id = ? AND revision = ?",
            (
                body.name,
                datetime.now(timezone.utc).isoformat(),
                document_id.hex,
                user["id"],
                body.revision,
            ),
        ).rowcount
        if changed != 1:
            raise HTTPException(409, "Tài liệu đã thay đổi. Hãy mở lại trước khi đổi tên.")
    return storage.summary(storage.document_row(settings, user["id"], document_id.hex))


@router.get("/{document_id}/assets/{storage_id}/{filename}")
def get_asset(
    document_id: UUID,
    storage_id: UUID,
    filename: str,
    request: Request,
    download: bool = False,
    user: dict = Depends(current_user),
):
    settings = request.app.state.settings
    row = storage.document_row(settings, user["id"], document_id.hex)
    if row["storage_id"] != storage_id.hex:
        raise HTTPException(404, "Phiên bản tài liệu không còn hiện hành. Hãy mở lại tài liệu.")
    return file_response(settings, row, filename, download)


@router.get("/{document_id}/pdf")
def get_pdf(
    document_id: UUID,
    request: Request,
    download: bool = False,
    revision: int | None = Query(None, ge=1),
    user: dict = Depends(current_user),
):
    settings = request.app.state.settings
    row = storage.document_row(settings, user["id"], document_id.hex)
    if revision is not None and row["revision"] != revision:
        raise HTTPException(409, "PDF đã được cập nhật. Hãy mở lại tài liệu.")
    return file_response(settings, row, "document.pdf", download)


def file_response(settings, row, filename: str, download: bool):
    directory = settings.data_dir / "documents" / row["id"] / row["storage_id"]
    if filename == "document.pdf":
        mime, suggested = "application/pdf", row["name"] + ".pdf"
    else:
        match = re.fullmatch(r"(original|rendered|thumbnail)_(0|[1-9]\d?)", filename)
        if not match or int(match[2]) >= row["page_count"]:
            raise HTTPException(404, "Không tìm thấy tệp.")
        page = storage.detail(settings, row)["pages"][int(match[2])]
        mime = "image/jpeg" if match[1] == "thumbnail" else page[match[1]]["mime"]
        suggested = (
            "page."
            + {
                "image/jpeg": "jpg",
                "image/png": "png",
                "image/webp": "webp",
                "image/avif": "avif",
                "image/heic": "heic",
                "image/heif": "heif",
            }[mime]
        )
    if settings.remote_storage:
        url = SupabaseStorage(settings).signed_url(
            f"{row['id']}/{row['storage_id']}/{filename}",
            suggested if download or mime in ("image/heic", "image/heif") else None,
        )
        return RedirectResponse(url, status_code=307, headers={"Referrer-Policy": "no-referrer"})
    if not (directory / filename).is_file():
        raise HTTPException(503, "Không thể mở tệp tài liệu. Vui lòng thử lại.")
    return FileResponse(
        directory / filename,
        media_type=mime,
        filename=suggested,
        content_disposition_type="attachment"
        if download or mime in ("image/heic", "image/heif")
        else "inline",
    )

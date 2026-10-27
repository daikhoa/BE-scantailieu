import hashlib
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pypdf.generic import ArrayObject
from starlette.datastructures import FormData, UploadFile

from .. import storage_gc
from ..config import Settings
from ..database import DATABASE_ERRORS, connect, write_lock
from ..object_storage import SupabaseStorage
from .models import ImageInfo, Manifest

logger = logging.getLogger(__name__)
FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "AVIF": "image/avif"}


def document_row(settings: Settings, user_id: str, identifier: str):
    with connect(settings) as db:
        row = db.execute(
            "SELECT * FROM documents WHERE id = ? AND user_id = ?", (identifier, user_id)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Không tìm thấy tài liệu.")
    return row


def summary(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "revision": row["revision"],
        "pageCount": row["page_count"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def write_uploaded_file(upload: UploadFile, target: Path, maximum: int) -> int:
    size = 0
    with target.open("xb") as destination:
        while chunk := upload.file.read(64 * 1024):
            size += len(chunk)
            if size > maximum:
                raise HTTPException(413, f"Một tệp vượt giới hạn {maximum // (1024 * 1024)} MiB.")
            destination.write(chunk)
    if not size:
        raise HTTPException(422, "Tệp rỗng không hợp lệ.")
    return size


def validate_image(path: Path, info: ImageInfo) -> None:
    if info.mime in ("image/heic", "image/heif"):
        # Originals are private opaque sources: the SDK validates/decompresses HEIF on reopening.
        # Never pass unsupported HEIF bytes to a server decoder or use them as a thumbnail.
        with path.open("rb") as source:
            header = source.read(64)
        if header[4:8] != b"ftyp" or not any(
            brand in header[8:] for brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1")
        ):
            raise HTTPException(422, "Ảnh HEIF không hợp lệ.")
        return
    try:
        with Image.open(path) as image:
            expected = (info.width, info.height)
            matches = image.size == expected
            orientation = image.getexif().get(274, 1) if not matches else 1
            if orientation in (5, 6, 7, 8):
                matches = matches or image.size == expected[::-1]
            if FORMATS.get(image.format) != info.mime or not matches:
                raise ValueError("Image metadata mismatch")
        # Reading EXIF may advance/close the PNG parser; verify a fresh handle as Pillow requires.
        with Image.open(path) as image:
            image.verify()
    except (UnidentifiedImageError, ValueError, SyntaxError, Image.DecompressionBombError, OSError):
        raise HTTPException(422, "Nội dung hoặc kích thước ảnh không hợp lệ.") from None


def validate_pdf(path: Path, page_count: int) -> None:
    try:
        with path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise ValueError("Invalid PDF header")
            source.seek(0)
            reader = PdfReader(source, strict=True)
            root = reader.trailer["/Root"]
            if reader.is_encrypted or any(
                key in root for key in ("/OpenAction", "/AA", "/AcroForm", "/Names")
            ):
                raise ValueError("Interactive PDF is not allowed")
            if len(reader.pages) != page_count:
                raise ValueError("PDF page mismatch")
            for page in reader.pages:
                if "/AA" in page:
                    raise ValueError("Page actions are not allowed")
                annotations = page.get("/Annots")
                if annotations is not None:
                    # pdf-lib emits /Annots [] on ordinary image pages; absence of content is safe.
                    annotations = annotations.get_object()
                    if not isinstance(annotations, ArrayObject) or annotations:
                        raise ValueError("Page annotations are not allowed")
    except (PdfReadError, ValueError, KeyError, TypeError, RecursionError):
        raise HTTPException(422, "PDF không hợp lệ hoặc số trang không khớp.") from None


def remove_version(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except OSError:
        # A concurrent download may still hold an open Windows file; the live version is safe.
        logger.warning(
            "An obsolete document version could not be removed; run storage cleanup when downloads finish."
        )


def save_document(
    settings: Settings,
    user_id: str,
    manifest: Manifest,
    form: FormData,
    identifier: str | None = None,
    create_key: str | None = None,
) -> dict:
    old = document_row(settings, user_id, identifier) if identifier else None
    if (old and manifest.revision != old["revision"]) or (
        not old and manifest.revision is not None
    ):
        raise HTTPException(409, "Tài liệu đã thay đổi. Hãy mở lại trước khi lưu.")
    if len(manifest.pages) > settings.max_pages:
        raise HTTPException(422, "Tài liệu có quá nhiều trang.")
    identifier = identifier or uuid.uuid4().hex
    storage_id = uuid.uuid4().hex
    directory = settings.data_dir / "documents" / identifier / storage_id
    expected = {"manifest", "pdf"} | {
        f"{kind}_{i}" for i in range(len(manifest.pages)) for kind in ("original", "rendered")
    }
    if set(form.keys()) != expected or len(form.multi_items()) != len(expected):
        raise HTTPException(422, "Danh sách tệp không khớp tài liệu.")
    committed = False
    try:
        directory.mkdir(parents=True)
        byte_size = 0
        for name in expected - {"manifest"}:
            upload = form[name]
            if not isinstance(upload, UploadFile):
                raise HTTPException(422, "Thiếu tệp tài liệu.")
            target = directory / ("document.pdf" if name == "pdf" else name)
            byte_size += write_uploaded_file(
                upload, target, settings.max_pdf_bytes if name == "pdf" else settings.max_file_bytes
            )
            if byte_size > settings.max_request_bytes:
                raise HTTPException(413, "Tài liệu vượt giới hạn dung lượng.")
        for index, page in enumerate(manifest.pages):
            validate_image(directory / f"original_{index}", page.original)
            validate_image(directory / f"rendered_{index}", page.rendered)
            with Image.open(directory / f"rendered_{index}") as image:
                image.thumbnail((320, 320))
                image.convert("RGB").save(directory / f"thumbnail_{index}", "JPEG", quality=80)
            byte_size += (directory / f"thumbnail_{index}").stat().st_size
        validate_pdf(directory / "document.pdf", len(manifest.pages))
        encoded = manifest.model_dump_json(exclude_none=True)
        (directory / "manifest.json").write_text(encoded, encoding="utf-8")
        byte_size += len(encoded.encode())
        now = datetime.now(timezone.utc).isoformat()
        fingerprint = hashlib.sha256(encoded.encode())
        if create_key:
            for filename in sorted(expected - {"manifest"}):
                with (directory / ("document.pdf" if filename == "pdf" else filename)).open(
                    "rb"
                ) as source:
                    fingerprint.update(hashlib.file_digest(source, "sha256").digest())
        if settings.remote_storage:
            with connect(settings) as pending_db:
                storage_gc.remember(
                    pending_db,
                    identifier,
                    storage_id,
                    sorted(path.name for path in directory.iterdir()),
                )
            remote = SupabaseStorage(settings)
            for path in directory.iterdir():
                if path.name == "document.pdf":
                    mime = "application/pdf"
                elif path.name == "manifest.json":
                    mime = "application/json"
                elif path.name.startswith("thumbnail_"):
                    mime = "image/jpeg"
                else:
                    kind, index = path.name.split("_")
                    page = manifest.pages[int(index)]
                    mime = (page.original if kind == "original" else page.rendered).mime
                remote.upload(path, f"{identifier}/{storage_id}/{path.name}", mime)
        with connect(settings) as db:
            write_lock(db, "documents:" + user_id)
            if create_key:
                receipt = db.execute(
                    "SELECT * FROM create_receipts WHERE user_id = ? AND request_key = ?",
                    (user_id, create_key),
                ).fetchone()
                if receipt:
                    previous = db.execute(
                        "SELECT * FROM documents WHERE id = ? AND user_id = ?",
                        (receipt["document_id"], user_id),
                    ).fetchone()
                    if (
                        receipt["fingerprint"] != fingerprint.hexdigest()
                        or not previous
                        or previous["revision"] != 1
                    ):
                        raise HTTPException(
                            409,
                            "Yêu cầu trước đã được lưu với dữ liệu khác. Hãy mở lại tài liệu trong kho.",
                        )
                    return summary(previous)
            usage = db.execute(
                "SELECT count(*) AS document_count, coalesce(sum(byte_size), 0) AS total_bytes FROM documents WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if usage["total_bytes"] - (
                old["byte_size"] if old else 0
            ) + byte_size > settings.max_user_bytes or (
                not old and usage["document_count"] >= settings.max_documents
            ):
                raise HTTPException(
                    413, "Kho tài liệu đã đạt giới hạn dung lượng hoặc số tài liệu."
                )
            if old:
                changed = db.execute(
                    "UPDATE documents SET name = ?, revision = revision + 1, storage_id = ?, page_count = ?, byte_size = ?, updated_at = ?, manifest = ? WHERE id = ? AND user_id = ? AND revision = ?",
                    (
                        manifest.name,
                        storage_id,
                        len(manifest.pages),
                        byte_size,
                        now,
                        encoded,
                        identifier,
                        user_id,
                        manifest.revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise HTTPException(409, "Tài liệu đã thay đổi. Hãy mở lại trước khi lưu.")
            else:
                db.execute(
                    "INSERT INTO documents (id,user_id,name,revision,storage_id,page_count,byte_size,created_at,updated_at,manifest) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
                    (
                        identifier,
                        user_id,
                        manifest.name,
                        storage_id,
                        len(manifest.pages),
                        byte_size,
                        now,
                        now,
                        encoded,
                    ),
                )
                if create_key:
                    db.execute(
                        "INSERT INTO create_receipts VALUES (?, ?, ?, ?)",
                        (user_id, create_key, fingerprint.hexdigest(), identifier),
                    )
            if settings.remote_storage:
                db.execute(
                    "DELETE FROM storage_garbage WHERE document_id = ? AND storage_id = ?",
                    (identifier, storage_id),
                )
                if old:
                    old_files = ["manifest.json", "document.pdf"] + [
                        f"{kind}_{index}"
                        for index in range(old["page_count"])
                        for kind in ("original", "rendered", "thumbnail")
                    ]
                    storage_gc.remember(db, identifier, old["storage_id"], old_files)
        committed = True
    except OSError:
        raise HTTPException(
            503, "Không thể ghi tài liệu. Bản đã lưu trước đó vẫn được giữ nguyên."
        ) from None
    finally:
        if (settings.remote_storage or not committed) and directory.exists():
            remove_version(directory)
    if old:
        if settings.remote_storage:
            try:
                storage_gc.collect_version(settings, identifier, old["storage_id"])
            except (HTTPException, *DATABASE_ERRORS):
                logger.warning("Obsolete cloud objects retained for a later storage cleanup.")
        else:
            remove_version(directory.parent / old["storage_id"])
    return summary(document_row(settings, user_id, identifier))


def detail(settings: Settings, row) -> dict:
    base = f"/api/v1/documents/{row['id']}/assets/{row['storage_id']}"
    try:
        path = settings.data_dir / "documents" / row["id"] / row["storage_id"] / "manifest.json"
        manifest = json.loads(row["manifest"] or path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(503, "Không thể mở dữ liệu tài liệu. Vui lòng thử lại.") from None
    return {
        **summary(row),
        "schemaVersion": 1,
        "pdfUrl": f"{base}/document.pdf",
        "pages": [
            {
                **page,
                "originalUrl": f"{base}/original_{i}",
                "renderedUrl": f"{base}/rendered_{i}",
                "thumbnailUrl": f"{base}/thumbnail_{i}",
            }
            for i, page in enumerate(manifest["pages"])
        ],
    }

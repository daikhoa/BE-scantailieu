"""Assemble edited pages with private stored pages without a client download round trip."""

from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from fastapi import HTTPException
from pypdf import PdfReader, PdfWriter
from starlette.datastructures import FormData, UploadFile

from ..object_storage import SupabaseStorage
from . import storage
from .models import Manifest, PageUpdate, ReusedPage, SavedPage


def save_page_update(settings, user_id: str, identifier: str, update: PageUpdate, form: FormData):
    old = storage.document_row(settings, user_id, identifier)
    if old["revision"] != update.revision:
        raise HTTPException(409, "Tài liệu đã thay đổi. Hãy mở lại trước khi lưu.")
    original = storage.detail(settings, old)
    indices = {page["id"]: index for index, page in enumerate(original["pages"])}
    changed = [index for index, page in enumerate(update.pages) if isinstance(page, SavedPage)]
    expected = (
        {"manifest"}
        | ({"pdf"} if changed else set())
        | {f"{kind}_{index}" for index in changed for kind in ("original", "rendered")}
    )
    if set(form.keys()) != expected or len(form.multi_items()) != len(expected):
        raise HTTPException(422, "Danh sách tệp không khớp phần thay đổi.")
    for page in update.pages:
        if isinstance(page, ReusedPage) and page.id not in indices:
            raise HTTPException(422, "Trang được dùng lại không thuộc phiên bản tài liệu này.")
    if (
        not changed
        and update.name == old["name"]
        and [page.id for page in update.pages] == list(indices)
    ):
        return storage.summary(old)

    with TemporaryDirectory(prefix="docscan-update-") as temporary, ExitStack() as handles:
        directory = Path(temporary)
        client = (
            handles.enter_context(
                httpx.Client(timeout=httpx.Timeout(45, connect=10), follow_redirects=False)
            )
            if settings.remote_storage
            else None
        )

        def read_old(filename, maximum):
            target = directory / filename
            if settings.remote_storage:
                SupabaseStorage(settings).download(
                    f"{identifier}/{old['storage_id']}/{filename}", target, maximum, client=client
                )
            else:
                source = settings.data_dir / "documents" / identifier / old["storage_id"] / filename
                if not source.is_file() or source.stat().st_size > maximum:
                    raise HTTPException(503, "Không đọc được phiên bản tài liệu đã lưu.")
                return source
            return target

        old_pdf = read_old("document.pdf", settings.max_pdf_bytes)
        reader = PdfReader(old_pdf, strict=True)
        edited_reader = None
        if changed:
            upload = form["pdf"]
            if not isinstance(upload, UploadFile):
                raise HTTPException(422, "Thiếu PDF của các trang thay đổi.")
            partial = directory / "edited.pdf"
            storage.write_uploaded_file(upload, partial, settings.max_pdf_bytes)
            storage.validate_pdf(partial, len(changed))
            edited_reader = PdfReader(partial, strict=True)
        writer = PdfWriter()
        resolved = []
        parts = []
        changed_position = 0
        total_bytes = 0
        for index, page in enumerate(update.pages):
            if isinstance(page, ReusedPage):
                old_index = indices[page.id]
                saved = original["pages"][old_index]
                resolved.append(
                    SavedPage.model_validate(
                        {
                            key: value
                            for key, value in saved.items()
                            if key not in ("originalUrl", "renderedUrl", "thumbnailUrl")
                        }
                    )
                )
                writer.add_page(reader.pages[old_index])
                for kind in ("original", "rendered"):
                    source = read_old(f"{kind}_{old_index}", settings.max_file_bytes)
                    total_bytes += source.stat().st_size
                    parts.append(
                        (
                            f"{kind}_{index}",
                            UploadFile(
                                handles.enter_context(source.open("rb")), filename=source.name
                            ),
                        )
                    )
            else:
                resolved.append(page)
                writer.add_page(edited_reader.pages[changed_position])
                changed_position += 1
                for kind in ("original", "rendered"):
                    upload = form[f"{kind}_{index}"]
                    if not isinstance(upload, UploadFile):
                        raise HTTPException(422, "Thiếu ảnh của trang thay đổi.")
                    total_bytes += upload.size or 0
                    parts.append((f"{kind}_{index}", upload))
            if total_bytes > settings.max_request_bytes:
                raise HTTPException(413, "Tài liệu vượt giới hạn dung lượng.")
        merged = directory / "merged.pdf"
        writer.write(merged)
        if merged.stat().st_size > settings.max_pdf_bytes:
            raise HTTPException(413, "PDF vượt giới hạn dung lượng.")
        parts.append(
            ("pdf", UploadFile(handles.enter_context(merged.open("rb")), filename="document.pdf"))
        )
        manifest = Manifest(
            schemaVersion=1, name=update.name, revision=update.revision, pages=resolved
        )
        parts.append(("manifest", manifest.model_dump_json()))
        return storage.save_document(settings, user_id, manifest, FormData(parts), identifier)

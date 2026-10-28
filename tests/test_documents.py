import io
import json
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject
from test_auth import ApiTestCase


def upload_payload(name="Bài học", revision=None, pages=2):
    image = io.BytesIO()
    Image.new("RGB", (12, 16), "white").save(image, format="JPEG")
    pdf = io.BytesIO()
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=12, height=16)
    writer.write(pdf)
    manifest = {"schemaVersion": 1, "name": name, "revision": revision, "pages": []}
    files = {"pdf": ("document.pdf", pdf.getvalue(), "application/pdf")}
    for index in range(pages):
        manifest["pages"].append(
            {
                "id": f"page-{index}",
                "original": {"width": 12, "height": 16, "mime": "image/jpeg"},
                "rendered": {"width": 12, "height": 16, "mime": "image/jpeg"},
                "recipe": {
                    "version": 1,
                    "crop": {"kind": "full-frame"},
                    "rotation": 0,
                    "dewarp": {"mode": "perspective-only"},
                    "filter": {"mode": "enhanced-color", "presetVersion": "test", "parameters": {}},
                },
                "engineVersion": "test",
                "recipeKey": f"key-{index}",
                "outcome": {"kind": "not-requested"},
            }
        )
        for kind in ("original", "rendered"):
            files[f"{kind}_{index}"] = ("image.jpg", image.getvalue(), "image/jpeg")
    return {"manifest": json.dumps(manifest)}, files


class DocumentTests(ApiTestCase):
    def test_accepts_phone_jpeg_with_multiple_picture_metadata(self):
        fields, files = upload_payload(pages=1)
        source = io.BytesIO()
        image = Image.new("RGB", (12, 16), "white")
        image.save(source, format="MPO", save_all=True, append_images=[image.copy()])
        files["original_0"] = ("phone.jpg", source.getvalue(), "image/jpeg")
        response = self.client.post(
            "/api/v1/documents", data=fields, files=files, headers=self.headers
        )
        self.assertEqual(response.status_code, 201, response.text)

    def test_accepts_encoded_or_oriented_dimensions_of_phone_jpegs(self):
        for orientation in range(1, 9):
            with self.subTest(orientation=orientation):
                fields, files = upload_payload(pages=1)
                source = io.BytesIO()
                image = Image.new("RGB", (16, 12), "white")
                exif = Image.Exif()
                exif[274] = orientation
                image.save(source, format="JPEG", exif=exif)
                manifest = json.loads(fields["manifest"])
                manifest["pages"][0]["original"].update(
                    width=12 if orientation >= 5 else 16,
                    height=16 if orientation >= 5 else 12,
                )
                fields["manifest"] = json.dumps(manifest)
                files["original_0"] = ("phone.jpg", source.getvalue(), "image/jpeg")
                response = self.client.post(
                    "/api/v1/documents", data=fields, files=files, headers=self.headers
                )
                self.assertEqual(response.status_code, 201, response.text)

    def test_identifies_the_invalid_page_and_preserves_its_previous_revision(self):
        document = self.save().json()
        fields, files = upload_payload(revision=1)
        files["original_1"] = ("broken.jpg", b"not an image", "image/jpeg")
        response = self.client.put(
            f"/api/v1/documents/{document['id']}", data=fields, files=files, headers=self.headers
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("Ảnh gốc trang 2", response.json()["detail"])
        self.assertEqual(
            self.client.get(f"/api/v1/documents/{document['id']}").json()["revision"], 1
        )

    def test_accepts_png_original_and_jpeg_rendered_page(self):
        fields, files = upload_payload(pages=1)
        source = io.BytesIO()
        Image.new("RGB", (12, 16), "white").save(source, format="PNG")
        manifest = json.loads(fields["manifest"])
        manifest["pages"][0]["original"]["mime"] = "image/png"
        fields["manifest"] = json.dumps(manifest)
        files["original_0"] = ("source.png", source.getvalue(), "image/png")
        response = self.client.post(
            "/api/v1/documents", data=fields, files=files, headers=self.headers
        )
        self.assertEqual(response.status_code, 201, response.text)

    def test_accepts_empty_annotation_arrays_emitted_by_pdf_lib_but_rejects_annotations(self):
        fields, files = upload_payload(pages=1)
        for with_annotation in (False, True):
            writer = PdfWriter()
            page = writer.add_blank_page(width=12, height=16)
            page[NameObject("/Annots")] = ArrayObject(
                [DictionaryObject({NameObject("/Subtype"): NameObject("/Link")})]
                if with_annotation
                else []
            )
            output = io.BytesIO()
            writer.write(output)
            files["pdf"] = ("document.pdf", output.getvalue(), "application/pdf")
            response = self.client.post(
                "/api/v1/documents", data=fields, files=files, headers=self.headers
            )
            self.assertEqual(response.status_code, 422 if with_annotation else 201, response.text)

    def test_retrying_the_same_create_request_does_not_duplicate_the_document(self):
        headers = {**self.headers, "Idempotency-Key": "request-id-for-retry-123"}
        fields, files = upload_payload()
        first = self.client.post("/api/v1/documents", data=fields, files=files, headers=headers)
        second = self.client.post("/api/v1/documents", data=fields, files=files, headers=headers)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.json()["id"], first.json()["id"])
        self.assertEqual(self.client.get("/api/v1/documents").json()["total"], 1)
        changed, changed_files = upload_payload(name="Other data")
        self.assertEqual(
            self.client.post(
                "/api/v1/documents", data=changed, files=changed_files, headers=headers
            ).status_code,
            409,
        )

    def setUp(self):
        super().setUp()
        self.register()
        self.login()

    def save(self, identifier=None, revision=None, pages=2):
        fields, files = upload_payload(revision=revision, pages=pages)
        method = self.client.put if identifier else self.client.post
        return method(
            f"/api/v1/documents/{identifier}" if identifier else "/api/v1/documents",
            data=fields,
            files=files,
            headers=self.headers,
        )

    def test_save_list_reopen_update_and_pdf(self):
        created = self.save()
        self.assertEqual(created.status_code, 201, created.text)
        document = created.json()
        identifier = document["id"]
        self.assertEqual(document["pageCount"], 2)
        listing = self.client.get("/api/v1/documents").json()
        self.assertEqual(listing["total"], 1)
        detail = self.client.get(f"/api/v1/documents/{identifier}").json()
        self.assertEqual([page["id"] for page in detail["pages"]], ["page-0", "page-1"])
        self.assertEqual(
            self.client.get(detail["pages"][0]["originalUrl"]).headers["content-type"], "image/jpeg"
        )
        pdf = self.client.get(detail["pdfUrl"] + "?download=true")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn("attachment", pdf.headers["content-disposition"])
        updated = self.save(identifier, revision=1, pages=1)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["revision"], 2)
        self.assertEqual(updated.json()["pageCount"], 1)
        self.assertEqual(self.client.get("/api/v1/documents").json()["total"], 1)
        self.assertEqual(self.save(identifier, revision=1).status_code, 409)

    def test_other_users_cannot_read_files_or_update_document(self):
        document = self.save().json()
        detail = self.client.get(f"/api/v1/documents/{document['id']}").json()
        self.client.delete("/api/v1/auth/session", headers=self.headers)
        self.register("other@example.com")
        self.login("other@example.com")
        self.assertEqual(self.client.get("/api/v1/documents").json()["total"], 0)
        self.assertEqual(self.client.get(f"/api/v1/documents/{document['id']}").status_code, 404)
        self.assertEqual(self.client.get(detail["pdfUrl"]).status_code, 404)
        self.assertEqual(self.client.get(detail["pages"][0]["originalUrl"]).status_code, 404)
        self.assertEqual(self.save(document["id"], revision=1).status_code, 404)

    def test_rejects_invalid_image_pdf_page_count_and_recipe_without_losing_old_version(self):
        document = self.save().json()
        for failure in ("image", "pdf", "count", "crop"):
            fields, files = upload_payload(revision=1)
            if failure == "image":
                files["original_0"] = ("evil.jpg", b"not an image", "image/jpeg")
            elif failure == "pdf":
                files["pdf"] = ("bad.pdf", b"%PDF broken", "application/pdf")
            elif failure == "count":
                files["pdf"] = upload_payload(pages=1)[1]["pdf"]
            else:
                manifest = json.loads(fields["manifest"])
                manifest["pages"][0]["recipe"]["rotation"] = 45
                fields["manifest"] = json.dumps(manifest)
            response = self.client.put(
                f"/api/v1/documents/{document['id']}",
                data=fields,
                files=files,
                headers=self.headers,
            )
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(
                self.client.get(f"/api/v1/documents/{document['id']}").json()["revision"], 1
            )

    def test_storage_failure_keeps_previous_revision(self):
        document = self.save().json()
        with patch(
            "app.documents.storage.write_uploaded_file", side_effect=OSError("disk unavailable")
        ):
            self.assertEqual(self.save(document["id"], revision=1).status_code, 503)
        self.assertEqual(
            self.client.get(f"/api/v1/documents/{document['id']}").json()["revision"], 1
        )

    def test_private_api_and_bounded_request(self):
        outsider = TestClient(self.client.app)
        self.assertEqual(outsider.get("/api/v1/documents").status_code, 401)
        self.assertEqual(outsider.post("/api/v1/documents").status_code, 401)
        outsider.close()
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/register",
                content=b"x" * 9000,
                headers={**self.headers, "Content-Type": "application/json"},
            ).status_code,
            413,
        )

    def test_old_pdf_link_does_not_silently_download_new_revision(self):
        document = self.save().json()
        self.assertEqual(
            self.client.get(
                f"/api/v1/documents/{document['id']}/pdf?revision=1&download=true"
            ).status_code,
            200,
        )
        self.save(document["id"], revision=1, pages=1)
        self.assertEqual(
            self.client.get(f"/api/v1/documents/{document['id']}/pdf?revision=1").status_code, 409
        )

    def test_cleanup_preserves_current_and_recent_staging_versions(self):
        import os
        import time

        from app.cleanup import cleanup

        document = self.save().json()
        root = self.settings.data_dir / "documents" / document["id"]
        abandoned = root / ("a" * 32)
        recent = root / ("b" * 32)
        abandoned.mkdir()
        recent.mkdir()
        (abandoned / "partial").write_bytes(b"partial")
        os.utime(abandoned, (time.time() - 7200, time.time() - 7200))
        self.assertEqual(cleanup(self.settings), 1)
        self.assertFalse(abandoned.exists())
        self.assertTrue(recent.exists())
        self.assertEqual(
            self.client.get(f"/api/v1/documents/{document['id']}/pdf").status_code, 200
        )

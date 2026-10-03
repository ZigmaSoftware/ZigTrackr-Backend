"""Attachment security tests (spec 31)."""

import io

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from apps.accounts.models import UserRole
from apps.bugs.models import BugAttachment
from apps.bugs.tests.factories import make_bug, make_masters, make_team, make_user
from apps.bugs.tests.test_api import PASSWORD, seed_rbac

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@override_settings(MEDIA_ROOT="/tmp/claude-1000/zbt-test-media")
class AttachmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.roles = seed_rbac()
        cls.team = make_team("T")
        cls.project, cls.module, cls.priority, cls.severity = make_masters()
        cls.dev = make_user("kiran", team=cls.team)
        cls.dev.set_password(PASSWORD); cls.dev.save()
        UserRole.objects.create(user=cls.dev, role=cls.roles["DEVELOPER"])
        cls.other = make_user("outsider")
        cls.other.set_password(PASSWORD); cls.other.save()
        UserRole.objects.create(user=cls.other, role=cls.roles["DEVELOPER"])

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.bug = make_bug(bug_no="ATT-1", reporter=self.dev, project=self.project,
                            priority=self.priority, severity=self.severity, owner=self.dev)

    def login(self, user):
        response = self.client.post(
            "/api/v1/auth/login/",
            data={"username": user.username, "password": PASSWORD},
            content_type="application/json")
        assert response.status_code == 200, response.content
        return self.client

    def _upload(self, name, content, content_type="image/png"):
        return self.client.post(
            f"/api/v1/bugs/{self.bug.unique_id}/attachments/",
            data={"file": SimpleUploadedFile(name, content, content_type=content_type)},
        )

    def test_valid_png_uploads(self):
        self.login(self.dev)
        response = self._upload("screenshot.png", PNG_BYTES)
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertEqual(data["file_name"], "screenshot.png")
        self.assertEqual(data["file_type"], "image/png")

    def test_stored_name_differs_from_uploaded_name(self):
        """Spec 31: never trust a user-provided filename on disk."""
        self.login(self.dev)
        self._upload("../../../etc/passwd.png", PNG_BYTES)
        attachment = BugAttachment.objects.get()
        self.assertNotIn("..", attachment.stored_file_name)
        self.assertNotIn("/", attachment.stored_file_name)
        self.assertNotIn("/", attachment.file_name)
        self.assertTrue(attachment.stored_file_name.endswith(".png"))

    def test_disallowed_extension_is_rejected(self):
        self.login(self.dev)
        response = self._upload("payload.exe", b"MZ\x90\x00", "application/x-msdownload")
        self.assertEqual(response.status_code, 400)
        self.assertIn("file", response.json()["errors"])

    def test_content_type_mismatch_is_rejected(self):
        """A PDF renamed .png must not pass: bytes are the evidence."""
        self.login(self.dev)
        response = self._upload("disguised.png", b"%PDF-1.4 fake pdf content here")
        self.assertEqual(response.status_code, 400)

    def test_client_content_type_is_not_trusted(self):
        """Client says image/png; bytes say otherwise. Bytes win."""
        self.login(self.dev)
        response = self._upload("evil.png", b"<html><script>alert(1)</script></html>",
                                content_type="image/png")
        self.assertEqual(response.status_code, 400)

    def test_empty_file_is_rejected(self):
        self.login(self.dev)
        response = self._upload("empty.png", b"")
        self.assertEqual(response.status_code, 400)

    @override_settings(ATTACHMENT_MAX_SIZE_MB=1)
    def test_oversized_file_is_rejected(self):
        self.login(self.dev)
        response = self._upload("big.png", PNG_BYTES + b"\x00" * (2 * 1024 * 1024))
        self.assertEqual(response.status_code, 400)

    def test_download_requires_bug_visibility(self):
        """The check that makes gating the download worthwhile."""
        self.login(self.dev)
        self._upload("shot.png", PNG_BYTES)
        attachment = BugAttachment.objects.get()

        self.client = Client()
        self.login(self.other)
        response = self.client.get(f"/api/v1/bugs/attachments/{attachment.unique_id}/download/")
        self.assertEqual(response.status_code, 404)

    def test_owner_can_download(self):
        self.login(self.dev)
        self._upload("shot.png", PNG_BYTES)
        attachment = BugAttachment.objects.get()
        response = self.client.get(f"/api/v1/bugs/attachments/{attachment.unique_id}/download/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("inline", response["Content-Disposition"])

    def test_non_image_downloads_as_attachment(self):
        self.login(self.dev)
        self._upload("data.csv", b"a,b,c\n1,2,3\n", "text/csv")
        attachment = BugAttachment.objects.get()
        response = self.client.get(f"/api/v1/bugs/attachments/{attachment.unique_id}/download/")
        self.assertEqual(response.status_code, 200)
        # Never inline: a CSV rendered in-page is a scripting surface.
        self.assertIn("attachment", response["Content-Disposition"])

    def test_anonymous_download_is_rejected(self):
        self.login(self.dev)
        self._upload("shot.png", PNG_BYTES)
        attachment = BugAttachment.objects.get()
        anonymous = Client()
        response = anonymous.get(f"/api/v1/bugs/attachments/{attachment.unique_id}/download/")
        self.assertEqual(response.status_code, 401)

    def test_media_root_is_not_publicly_routed(self):
        """Spec 31: static serving of MEDIA_ROOT would bypass every check."""
        self.login(self.dev)
        self._upload("shot.png", PNG_BYTES)
        attachment = BugAttachment.objects.get()
        response = self.client.get(f"/media/{attachment.file_path}")
        self.assertIn(response.status_code, (404, 301),
                      "MEDIA_ROOT must not be served directly")

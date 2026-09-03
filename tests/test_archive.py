import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import archive


class FakeUploadHttp:
    def __init__(self):
        self.requests = []
        self.parts = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs.get("body")))
        if url.endswith("/file_uploads"):
            return {
                "id": "UPLOAD_ID",
                "status": "pending",
                "upload_url": "https://upload.example/send",
                "complete_url": "https://upload.example/complete",
            }
        if url.endswith("/complete"):
            return {"id": "UPLOAD_ID", "status": "uploaded"}
        raise AssertionError(url)

    def multipart(self, url, **kwargs):
        self.parts.append((kwargs.get("part_number"), kwargs["content"]))
        return {
            "id": "UPLOAD_ID",
            "status": "uploaded" if kwargs.get("part_number") is None else "pending",
        }


class FakeSlackHttp:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        slack_method = url.rsplit("/", 1)[-1]
        params = kwargs.get("params") or kwargs.get("body") or {}
        self.calls.append((method, slack_method, params))
        if slack_method == "conversations.list":
            return {
                "ok": True,
                "channels": [
                    {"id": "C_JOIN", "name": "product", "is_member": False, "is_private": False},
                    {"id": "C_SKIP", "name": "random", "is_member": False, "is_private": False},
                    {"id": "G_PRIVATE", "name": "leaders", "is_member": False, "is_private": True},
                ],
                "response_metadata": {"next_cursor": ""},
            }
        if slack_method == "conversations.join":
            return {
                "ok": True,
                "channel": {
                    "id": params["channel"],
                    "name": "product",
                    "is_member": True,
                    "is_private": False,
                },
            }
        raise AssertionError(slack_method)


class ArchiveTests(unittest.TestCase):
    def test_defaults_to_previous_kst_month(self):
        now = datetime(2026, 9, 3, 6, 0, tzinfo=timezone.utc)
        window = archive.month_window(None, now)
        self.assertEqual(window.label, "2026-08")
        self.assertEqual(window.start.isoformat(), "2026-08-01T00:00:00+09:00")
        self.assertEqual(window.end.isoformat(), "2026-09-01T00:00:00+09:00")

    def test_december_rolls_into_next_year(self):
        window = archive.month_window("2026-12")
        self.assertEqual(window.end.isoformat(), "2027-01-01T00:00:00+09:00")

    def test_slack_markup_becomes_readable(self):
        value = archive.slack_text(
            "<@U1> <https://example.com|문서> <https://openai.com>",
            {"U1": "민수"},
        )
        self.assertEqual(value, "@민수 문서 (https://example.com) https://openai.com")

    def test_mock_builds_thread_blocks_and_preview(self):
        window = archive.month_window("2026-08")
        channels, users = archive.mock_archive(window)
        channel = channels[0]
        blocks = archive.archive_blocks(channel, channel["messages"], users, window, "https://demo.slack.com")
        preview = archive.markdown_preview(
            channel, channel["messages"], users, window, "https://demo.slack.com"
        )
        self.assertGreaterEqual(len(blocks), 7)
        self.assertIn("↳ **08-03 09:20 서연**", preview)
        self.assertIn("@서연 QA 일정", preview)
        self.assertIn("https://demo.slack.com/archives/C01PRODUCT/", preview)

    def test_chunked_never_exceeds_notion_limit(self):
        batches = list(archive.chunked(list(range(251)), 100))
        self.assertEqual([len(batch) for batch in batches], [100, 100, 51])

    def test_long_text_is_split(self):
        pieces = archive.rich_text("가" * 4001)
        self.assertEqual([len(item["text"]["content"]) for item in pieces], [1900, 1900, 201])

    def test_content_type_rejects_header_injection(self):
        self.assertEqual(archive.safe_content_type("image/png"), "image/png")
        self.assertEqual(
            archive.safe_content_type("image/png\r\nX-Injected: yes"),
            "application/octet-stream",
        )

    def test_environment_flag_is_strict(self):
        with mock.patch.dict(os.environ, {"AUTO_JOIN_PUBLIC_CHANNELS": "true"}):
            self.assertTrue(archive.environment_flag("AUTO_JOIN_PUBLIC_CHANNELS"))
        with mock.patch.dict(os.environ, {"AUTO_JOIN_PUBLIC_CHANNELS": "invalid"}):
            with self.assertRaises(archive.ArchiveError):
                archive.environment_flag("AUTO_JOIN_PUBLIC_CHANNELS")

    def test_auto_join_only_selected_public_channels(self):
        fake = FakeSlackHttp()
        slack = archive.SlackClient("token", http=fake)
        channels = slack.member_channels({"product"}, auto_join_public=True)
        self.assertEqual([channel["id"] for channel in channels], ["C_JOIN"])
        join_calls = [
            (http_method, params)
            for http_method, slack_method, params in fake.calls
            if slack_method == "conversations.join"
        ]
        self.assertEqual(join_calls, [("POST", {"channel": "C_JOIN"})])

    def test_auto_join_requires_channel_allowlist(self):
        slack = archive.SlackClient("token", http=FakeSlackHttp())
        with self.assertRaises(archive.ArchiveError):
            slack.member_channels(None, auto_join_public=True)

    def test_uploaded_image_becomes_notion_image_block(self):
        window = archive.month_window("2026-08")
        channels, users = archive.mock_archive(window)
        image = channels[0]["messages"][1]["files"][0]
        image["_notion_upload_id"] = "UPLOAD_ID"
        blocks = archive.archive_blocks(
            channels[0], channels[0]["messages"], users, window, "https://demo.slack.com"
        )
        image_blocks = [block for block in blocks if block["type"] == "image"]
        self.assertEqual(len(image_blocks), 1)
        self.assertEqual(image_blocks[0]["image"]["file_upload"]["id"], "UPLOAD_ID")

    def test_notion_uses_single_part_for_small_image(self):
        with tempfile.NamedTemporaryFile(delete=False) as temp:
            temp.write(b"small-image")
            path = temp.name
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        fake = FakeUploadHttp()
        notion = archive.NotionClient("token", "parent", http=fake)
        downloaded = archive.DownloadedFile(path, "image.png", "image/png", 11)
        with mock.patch.object(archive.time, "sleep"):
            upload_id = notion.upload_file(downloaded)
        self.assertEqual(upload_id, "UPLOAD_ID")
        self.assertEqual(fake.parts, [(None, b"small-image")])
        self.assertEqual(fake.requests[0][2]["mode"], "single_part")

    def test_notion_uses_numbered_parts_for_large_image(self):
        with tempfile.NamedTemporaryFile(delete=False) as temp:
            temp.write(b"x" * 25)
            path = temp.name
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        fake = FakeUploadHttp()
        notion = archive.NotionClient("token", "parent", http=fake)
        downloaded = archive.DownloadedFile(path, "large.png", "image/png", 25)
        with (
            mock.patch.object(archive, "NOTION_SINGLE_PART_LIMIT_BYTES", 20),
            mock.patch.object(archive, "NOTION_PART_BYTES", 10),
            mock.patch.object(archive.time, "sleep"),
        ):
            notion.upload_file(downloaded)
        self.assertEqual([(number, len(data)) for number, data in fake.parts], [(1, 10), (2, 10), (3, 5)])
        self.assertEqual(fake.requests[0][2]["number_of_parts"], 3)
        self.assertTrue(fake.requests[-1][1].endswith("/complete"))


if __name__ == "__main__":
    unittest.main()

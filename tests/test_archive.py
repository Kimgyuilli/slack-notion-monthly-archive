import email.message
import io
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock
from urllib.error import HTTPError, URLError

import archive
from app import http_client, models, notion_client, renderer, slack_client


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


class FakeNotionDatabaseHttp:
    def __init__(
        self,
        *,
        include_status=True,
        existing=None,
        fail_blocks=False,
        channel_options=None,
        period_options=None,
    ):
        self.include_status = include_status
        self.existing = existing
        self.fail_blocks = fail_blocks
        self.channel_options = channel_options or []
        self.period_options = period_options or []
        self.requests = []

    def request(self, method, url, **kwargs):
        body = kwargs.get("body")
        self.requests.append((method, url, body))
        if method == "GET" and url.endswith("/data_sources/DATA_SOURCE"):
            properties = {
                "이름": {"type": "title", "title": {}},
                "채널": {
                    "type": "select",
                    "select": {"options": self.channel_options},
                },
                "기간": {
                    "type": "select",
                    "select": {"options": self.period_options},
                },
            }
            if self.include_status:
                properties["상태"] = {
                    "type": "status",
                    "status": {
                        "options": [
                            {"name": "진행 중"},
                            {"name": "완료"},
                            {"name": "실패"},
                        ]
                    },
                }
            return {"id": "DATA_SOURCE", "properties": properties}
        if method == "POST" and url.endswith("/data_sources/DATA_SOURCE/query"):
            return {"results": [self.existing] if self.existing else []}
        if method == "POST" and url.endswith("/pages"):
            return {"id": "PAGE_ID", "url": "https://notion.example/PAGE_ID"}
        if method == "PATCH" and url.endswith("/data_sources/DATA_SOURCE"):
            return {"id": "DATA_SOURCE"}
        if method == "PATCH" and "/blocks/" in url:
            if self.fail_blocks:
                raise models.ArchiveError("block append failed")
            return {}
        if method == "PATCH" and url.endswith("/pages/PAGE_ID"):
            return {"id": "PAGE_ID"}
        raise AssertionError((method, url))


class ArchiveTests(unittest.TestCase):
    def test_defaults_to_previous_kst_month(self):
        now = datetime(2026, 9, 3, 6, 0, tzinfo=timezone.utc)
        window = models.month_window(None, now)
        self.assertEqual(window.label, "2026-08")
        self.assertEqual(window.start.isoformat(), "2026-08-01T00:00:00+09:00")
        self.assertEqual(window.end.isoformat(), "2026-09-01T00:00:00+09:00")

    def test_december_rolls_into_next_year(self):
        window = models.month_window("2026-12")
        self.assertEqual(window.end.isoformat(), "2027-01-01T00:00:00+09:00")

    def test_slack_markup_becomes_readable(self):
        value = renderer.slack_text(
            "<@U1> <https://example.com|문서> <https://openai.com>",
            {"U1": "민수"},
        )
        self.assertEqual(value, "@민수 문서 (https://example.com) https://openai.com")

    def test_mock_builds_thread_blocks_and_preview(self):
        window = models.month_window("2026-08")
        channels, users = archive.mock_archive(window)
        channel = channels[0]
        blocks = renderer.archive_blocks(channel, channel["messages"], users, window, "https://demo.slack.com")
        preview = renderer.markdown_preview(
            channel, channel["messages"], users, window, "https://demo.slack.com"
        )
        self.assertGreaterEqual(len(blocks), 7)
        self.assertIn("↳ **08-03 09:20 서연**", preview)
        self.assertIn("@서연 QA 일정", preview)
        self.assertIn("https://demo.slack.com/archives/C01PRODUCT/", preview)
        self.assertIn("채널=#product · 기간=2026-08 · 상태=완료", preview)

    def test_chunked_never_exceeds_notion_limit(self):
        batches = list(models.chunked(list(range(251)), 100))
        self.assertEqual([len(batch) for batch in batches], [100, 100, 51])

    def test_long_text_is_split(self):
        pieces = renderer.rich_text("가" * 4001)
        self.assertEqual([len(item["text"]["content"]) for item in pieces], [1900, 1900, 201])

    def test_content_type_rejects_header_injection(self):
        self.assertEqual(slack_client.safe_content_type("image/png"), "image/png")
        self.assertEqual(
            slack_client.safe_content_type("image/png\r\nX-Injected: yes"),
            "application/octet-stream",
        )

    def test_environment_flag_is_strict(self):
        with mock.patch.dict(os.environ, {"AUTO_JOIN_PUBLIC_CHANNELS": "true"}):
            self.assertTrue(archive.environment_flag("AUTO_JOIN_PUBLIC_CHANNELS"))
        with mock.patch.dict(os.environ, {"AUTO_JOIN_PUBLIC_CHANNELS": "invalid"}):
            with self.assertRaises(models.ArchiveError):
                archive.environment_flag("AUTO_JOIN_PUBLIC_CHANNELS")

    def test_auto_join_all_public_channels_and_keeps_member_private_channels(self):
        fake = FakeSlackHttp()
        slack = slack_client.SlackClient("token", http=fake)
        channels = slack.member_channels(auto_join_public=True)
        self.assertEqual([channel["id"] for channel in channels], ["C_JOIN", "C_SKIP"])
        join_calls = [
            (http_method, params)
            for http_method, slack_method, params in fake.calls
            if slack_method == "conversations.join"
        ]
        self.assertEqual(
            join_calls,
            [
                ("POST", {"channel": "C_JOIN"}),
                ("POST", {"channel": "C_SKIP"}),
            ],
        )

    def test_auto_join_false_only_returns_member_channels(self):
        fake = FakeSlackHttp()
        fake_channel_list = fake.request

        def request(method, url, **kwargs):
            if url.endswith("/conversations.list"):
                return {
                    "ok": True,
                    "channels": [
                        {"id": "C_MEMBER", "name": "general", "is_member": True, "is_private": False},
                        {"id": "C_PUBLIC", "name": "random", "is_member": False, "is_private": False},
                        {"id": "G_MEMBER", "name": "leaders", "is_member": True, "is_private": True},
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            return fake_channel_list(method, url, **kwargs)

        fake.request = request
        slack = slack_client.SlackClient("token", http=fake)
        channels = slack.member_channels(auto_join_public=False)
        self.assertEqual([channel["id"] for channel in channels], ["C_MEMBER", "G_MEMBER"])
        self.assertFalse(
            any(slack_method == "conversations.join" for _, slack_method, _ in fake.calls)
        )

    def test_notion_database_schema_is_validated(self):
        notion = notion_client.NotionClient("token", "DATA_SOURCE", http=FakeNotionDatabaseHttp())
        notion.validate_schema()

        missing_status = notion_client.NotionClient(
            "token",
            "DATA_SOURCE",
            http=FakeNotionDatabaseHttp(include_status=False),
        )
        with self.assertRaisesRegex(models.ArchiveError, "상태\\(status\\)"):
            missing_status.validate_schema()

    def test_notion_duplicate_query_uses_channel_and_period(self):
        existing = {"id": "EXISTING", "url": "https://notion.example/EXISTING"}
        fake = FakeNotionDatabaseHttp(existing=existing)
        notion = notion_client.NotionClient("token", "DATA_SOURCE", http=fake)
        result = notion.exact_entry("#product", "2026-08")
        self.assertEqual(result, existing)
        query = fake.requests[-1][2]
        self.assertEqual(
            query["filter"]["and"],
            [
                {"property": "채널", "select": {"equals": "#product"}},
                {"property": "기간", "select": {"equals": "2026-08"}},
            ],
        )

    def test_notion_adds_missing_channel_and_period_labels(self):
        fake = FakeNotionDatabaseHttp(
            channel_options=[{"id": "CHANNEL_PRODUCT", "name": "#product"}],
            period_options=[{"id": "PERIOD_AUGUST", "name": "2026-08"}],
        )
        notion = notion_client.NotionClient("token", "DATA_SOURCE", http=fake)
        properties = notion.validate_schema()
        added = notion.ensure_select_options(
            properties,
            {"#product", "#archive-test"},
            "2026-09",
        )
        self.assertEqual(
            added,
            {"채널": ["#archive-test"], "기간": ["2026-09"]},
        )
        update = fake.requests[-1][2]["properties"]
        self.assertEqual(
            update["채널"]["select"]["options"],
            [{"id": "CHANNEL_PRODUCT"}, {"name": "#archive-test"}],
        )
        self.assertEqual(
            update["기간"]["select"]["options"],
            [{"id": "PERIOD_AUGUST"}, {"name": "2026-09"}],
        )

    def test_notion_does_not_update_existing_labels(self):
        fake = FakeNotionDatabaseHttp(
            channel_options=[{"id": "CHANNEL_PRODUCT", "name": "#product"}],
            period_options=[{"id": "PERIOD_AUGUST", "name": "2026-08"}],
        )
        notion = notion_client.NotionClient("token", "DATA_SOURCE", http=fake)
        properties = notion.validate_schema()
        added = notion.ensure_select_options(properties, {"#product"}, "2026-08")
        self.assertEqual(added, {})
        self.assertFalse(
            any(
                method == "PATCH" and url.endswith("/data_sources/DATA_SOURCE")
                for method, url, _ in fake.requests
            )
        )

    def test_notion_database_entry_sets_labels_and_status(self):
        fake = FakeNotionDatabaseHttp()
        notion = notion_client.NotionClient("token", "DATA_SOURCE", http=fake)
        with mock.patch.object(notion_client.time, "sleep"):
            result = notion.create_archive_entry(
                "Slack · 2026-08 · #product",
                "#product",
                "2026-08",
                [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}],
            )
        self.assertEqual(result["status"], "created")
        create_body = next(
            body
            for method, url, body in fake.requests
            if method == "POST" and url.endswith("/pages")
        )
        self.assertEqual(
            create_body["parent"],
            {"type": "data_source_id", "data_source_id": "DATA_SOURCE"},
        )
        self.assertEqual(create_body["properties"]["채널"]["select"]["name"], "#product")
        self.assertEqual(create_body["properties"]["기간"]["select"]["name"], "2026-08")
        self.assertEqual(create_body["properties"]["상태"]["status"]["name"], "진행 중")
        status_updates = [
            body["properties"]["상태"]["status"]["name"]
            for method, url, body in fake.requests
            if method == "PATCH" and url.endswith("/pages/PAGE_ID")
        ]
        self.assertEqual(status_updates, ["완료"])

    def test_notion_database_entry_marks_failed_when_blocks_fail(self):
        fake = FakeNotionDatabaseHttp(fail_blocks=True)
        notion = notion_client.NotionClient("token", "DATA_SOURCE", http=fake)
        with self.assertRaisesRegex(models.ArchiveError, "block append failed"):
            notion.create_archive_entry(
                "Slack · 2026-08 · #product",
                "#product",
                "2026-08",
                [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}],
            )
        status_updates = [
            body["properties"]["상태"]["status"]["name"]
            for method, url, body in fake.requests
            if method == "PATCH" and url.endswith("/pages/PAGE_ID")
        ]
        self.assertEqual(status_updates, ["실패"])

    def test_uploaded_image_becomes_notion_image_block(self):
        window = models.month_window("2026-08")
        channels, users = archive.mock_archive(window)
        image = channels[0]["messages"][1]["files"][0]
        image["_notion_upload_id"] = "UPLOAD_ID"
        blocks = renderer.archive_blocks(
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
        notion = notion_client.NotionClient("token", "parent", http=fake)
        downloaded = models.DownloadedFile(path, "image.png", "image/png", 11)
        with mock.patch.object(notion_client.time, "sleep"):
            upload_id = notion.upload_file(downloaded)
        self.assertEqual(upload_id, "UPLOAD_ID")
        self.assertEqual(fake.parts, [(None, b"small-image")])
        self.assertEqual(fake.requests[0][2]["mode"], "single_part")

    def test_notion_uses_single_part_between_part_size_and_limit(self):
        """A 10-20 MiB image is one single_part upload, not two reads."""
        with tempfile.NamedTemporaryFile(delete=False) as temp:
            temp.write(b"x" * 15)
            path = temp.name
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        fake = FakeUploadHttp()
        notion = notion_client.NotionClient("token", "parent", http=fake)
        downloaded = models.DownloadedFile(path, "medium.png", "image/png", 15)
        with (
            mock.patch.object(notion_client, "NOTION_SINGLE_PART_LIMIT_BYTES", 20),
            mock.patch.object(notion_client, "NOTION_PART_BYTES", 10),
            mock.patch.object(notion_client.time, "sleep"),
        ):
            upload_id = notion.upload_file(downloaded)
        self.assertEqual(upload_id, "UPLOAD_ID")
        self.assertEqual(fake.parts, [(None, b"x" * 15)])
        self.assertEqual(fake.requests[0][2]["mode"], "single_part")
        self.assertNotIn("number_of_parts", fake.requests[0][2])

    def test_retry_delay_prefers_numeric_retry_after(self):
        self.assertEqual(http_client.retry_delay("2.5", 0), 2.5)
        self.assertEqual(http_client.retry_delay("Wed, 21 Oct 2026 07:28:00 GMT", 3), 8)
        self.assertEqual(http_client.retry_delay(None, 9), 16)

    def test_notion_uses_numbered_parts_for_large_image(self):
        with tempfile.NamedTemporaryFile(delete=False) as temp:
            temp.write(b"x" * 25)
            path = temp.name
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        fake = FakeUploadHttp()
        notion = notion_client.NotionClient("token", "parent", http=fake)
        downloaded = models.DownloadedFile(path, "large.png", "image/png", 25)
        with (
            mock.patch.object(notion_client, "NOTION_SINGLE_PART_LIMIT_BYTES", 20),
            mock.patch.object(notion_client, "NOTION_PART_BYTES", 10),
            mock.patch.object(notion_client.time, "sleep"),
        ):
            notion.upload_file(downloaded)
        self.assertEqual([(number, len(data)) for number, data in fake.parts], [(1, 10), (2, 10), (3, 5)])
        self.assertEqual(fake.requests[0][2]["number_of_parts"], 3)
        self.assertTrue(fake.requests[-1][1].endswith("/complete"))


def http_error(code, *, body=b"boom", retry_after=None, cleanup=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    error = HTTPError("https://api.example/x", code, "err", headers, io.BytesIO(body))
    if cleanup:
        cleanup(error.close)
    return error


class FakeResponse:
    """Minimal stand-in for the object urlopen yields as a context manager."""

    def __init__(self, body=b"{}", content_type="application/json"):
        self._body = body
        self._offset = 0
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type

    def read(self, size=-1):
        if size is None or size < 0:
            chunk, self._offset = self._body[self._offset:], len(self._body)
            return chunk
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class HttpClientTests(unittest.TestCase):
    """The retry policy every Slack and Notion call depends on."""

    def setUp(self):
        self.sleeps = []
        patcher = mock.patch.object(
            http_client.time, "sleep", side_effect=self.sleeps.append
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_retries_5xx_then_succeeds(self):
        responses = [http_error(500, cleanup=self.addCleanup), http_error(503, cleanup=self.addCleanup), FakeResponse(b'{"ok": true}')]
        with mock.patch.object(
            http_client, "urlopen", side_effect=responses
        ) as urlopen:
            result = http_client.JsonHttpClient().request("GET", "https://api.example/x")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(self.sleeps, [1, 2])

    def test_does_not_retry_client_errors(self):
        with mock.patch.object(
            http_client, "urlopen", side_effect=http_error(404, cleanup=self.addCleanup)
        ) as urlopen:
            with self.assertRaisesRegex(models.ArchiveError, "HTTP 404"):
                http_client.JsonHttpClient().request("GET", "https://api.example/x")
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(self.sleeps, [])

    def test_gives_up_after_configured_retries(self):
        with mock.patch.object(
            http_client, "urlopen", side_effect=http_error(500, cleanup=self.addCleanup, body=b"upstream down")
        ) as urlopen:
            with self.assertRaisesRegex(models.ArchiveError, "upstream down"):
                http_client.JsonHttpClient(retries=2).request("GET", "https://api.example/x")
        self.assertEqual(urlopen.call_count, 3)

    def test_network_errors_retry_then_wrap(self):
        with mock.patch.object(
            http_client, "urlopen", side_effect=URLError("no route")
        ) as urlopen:
            with self.assertRaisesRegex(models.ArchiveError, "네트워크 요청 실패"):
                http_client.JsonHttpClient(retries=1).request("GET", "https://api.example/x")
        self.assertEqual(urlopen.call_count, 2)

    def test_retry_after_header_is_honoured_and_clamped(self):
        responses = [http_error(429, cleanup=self.addCleanup, retry_after="3600"), FakeResponse()]
        with mock.patch.object(http_client, "urlopen", side_effect=responses):
            http_client.JsonHttpClient().request("GET", "https://api.example/x")
        self.assertEqual(self.sleeps, [16])

    def test_multipart_wraps_errors_with_upload_prefix(self):
        with mock.patch.object(http_client, "urlopen", side_effect=http_error(500, cleanup=self.addCleanup)):
            with self.assertRaisesRegex(models.ArchiveError, "파일 업로드 HTTP 500"):
                http_client.JsonHttpClient(retries=0).multipart(
                    "https://api.example/upload",
                    headers={},
                    content=b"data",
                    filename="사진.png",
                    content_type="image/png",
                )

    def test_none_params_are_dropped_from_the_query_string(self):
        with mock.patch.object(
            http_client, "urlopen", return_value=FakeResponse()
        ) as urlopen:
            http_client.JsonHttpClient().request(
                "GET",
                "https://api.example/x",
                params={"cursor": None, "limit": 200},
            )
        self.assertEqual(urlopen.call_args[0][0].full_url, "https://api.example/x?limit=200")

    def test_empty_body_becomes_empty_dict(self):
        with mock.patch.object(http_client, "urlopen", return_value=FakeResponse(b"")):
            self.assertEqual(
                http_client.JsonHttpClient().request("GET", "https://api.example/x"), {}
            )


class MessageWindowTests(unittest.TestCase):
    """Only messages inside the target month may be archived."""

    def setUp(self):
        self.window = models.month_window("2026-08")

    def ts(self, year, month, day):
        return f"{datetime(year, month, day, 12, 0, tzinfo=models.KST).timestamp():.6f}"

    def test_contains_is_half_open(self):
        self.assertFalse(self.window.contains(f"{self.window.start.timestamp() - 1:.6f}"))
        self.assertTrue(self.window.contains(f"{self.window.start.timestamp():.6f}"))
        self.assertFalse(self.window.contains(f"{self.window.end.timestamp():.6f}"))

    def test_channel_messages_drops_out_of_window_roots_and_replies(self):
        inside, before, after = self.ts(2026, 8, 15), self.ts(2026, 7, 31), self.ts(2026, 9, 1)
        reply_inside = self.ts(2026, 8, 16)

        class Fake:
            def request(_, method, url, **kwargs):
                params = kwargs.get("params") or {}
                if url.endswith("conversations.history"):
                    return {
                        "ok": True,
                        "messages": [
                            {"ts": after, "text": "next month"},
                            {"ts": inside, "text": "keep", "reply_count": 2},
                            {"ts": before, "text": "last month"},
                        ],
                        "response_metadata": {"next_cursor": ""},
                    }
                if url.endswith("conversations.replies"):
                    return {
                        "ok": True,
                        "messages": [
                            {"ts": params["ts"], "text": "root echo"},
                            {"ts": after, "text": "late reply"},
                            {"ts": reply_inside, "text": "kept reply"},
                        ],
                        "response_metadata": {"next_cursor": ""},
                    }
                raise AssertionError(url)

        messages = slack_client.SlackClient("token", http=Fake()).channel_messages(
            "C1", self.window
        )
        self.assertEqual([message["text"] for message in messages], ["keep"])
        self.assertEqual([r["text"] for r in messages[0]["_replies"]], ["kept reply"])


class DownloadTests(unittest.TestCase):
    def test_declared_size_over_the_limit_is_rejected_before_download(self):
        slack = slack_client.SlackClient("token", http=http_client.JsonHttpClient())
        with self.assertRaisesRegex(models.ArchiveError, "한도"):
            slack.download_file(
                {"url_private_download": "https://files.example/a.png", "size": 5_000_000},
                1_000_000,
            )

    def test_missing_download_url_is_reported(self):
        slack = slack_client.SlackClient("token", http=http_client.JsonHttpClient())
        with self.assertRaisesRegex(models.ArchiveError, "다운로드 URL"):
            slack.download_file({"name": "a.png"}, 1_000_000)

    def test_streaming_past_the_limit_aborts_and_removes_the_temp_file(self):
        slack = slack_client.SlackClient("token", http=http_client.JsonHttpClient())
        before = set(os.listdir(tempfile.gettempdir()))
        with mock.patch.object(
            slack_client, "urlopen", return_value=FakeResponse(b"x" * 4096, "image/png")
        ):
            with self.assertRaisesRegex(models.ArchiveError, "다운로드 중"):
                slack.download_file(
                    {"url_private_download": "https://files.example/a.png", "name": "a.png"},
                    10,
                )
        leaked = [
            name
            for name in set(os.listdir(tempfile.gettempdir())) - before
            if name.startswith("slack-image-")
        ]
        self.assertEqual(leaked, [])

    def test_successful_download_reports_size_and_response_content_type(self):
        slack = slack_client.SlackClient("token", http=http_client.JsonHttpClient())
        with mock.patch.object(
            slack_client, "urlopen", return_value=FakeResponse(b"y" * 2048, "image/webp")
        ):
            downloaded = slack.download_file(
                {"url_private_download": "https://files.example/a.png",
                 "name": "a.png", "mimetype": "image/png"},
                1_000_000,
            )
        self.addCleanup(downloaded.cleanup)
        self.assertEqual(downloaded.size, 2048)
        self.assertEqual(downloaded.content_type, "image/webp")
        self.assertEqual(downloaded.filename, "a.png")


if __name__ == "__main__":
    unittest.main()

"""Tests for the Microsoft Graph OneNote importer.

Three seams reach outside this module: Graph's HTTP API (`requests`), MSAL's
device-code login (`msal`), and `onenote_import.import_pages`, which already
has its own coverage in `test_onenote_import.py` and is not retested here.
Everything below either mocks `requests.get`/`.text` directly or is a pure
function (`html_to_text`) that needs no mock at all.

`unittest` and the `sys.path` insert match `test_keep_import.py`.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "src" / "integrations"))

import onenote_graph_import as ogi  # noqa: E402


def _response(json_payload=None, text=None, status=200):
    resp = MagicMock()
    resp.status_code = status
    if json_payload is not None:
        resp.json.return_value = json_payload
    if text is not None:
        resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


class TestHtmlToText(unittest.TestCase):
    def test_layout_markup_is_stripped_to_plain_lines(self):
        """OneNote's export is absolute-positioned <div>s per paragraph, no
        semantic structure — this just needs the words, in order."""
        html = (
            "<html><body>"
            "<div><p>Apple Pear and Hazelnut Yogurt</p></div>"
            "<div><p>200g Greek yogurt</p></div>"
            "<div><p>1 apple, diced</p></div>"
            "</body></html>"
        )
        text = ogi.html_to_text(html)
        self.assertEqual(
            text,
            "Apple Pear and Hazelnut Yogurt\n200g Greek yogurt\n1 apple, diced",
        )

    def test_blank_lines_are_dropped(self):
        html = "<div><p>Title</p></div><div>   </div><div><p>Body</p></div>"
        self.assertEqual(ogi.html_to_text(html), "Title\nBody")

    def test_empty_html_is_empty_text(self):
        self.assertEqual(ogi.html_to_text(""), "")


class TestFindSection(unittest.TestCase):
    @patch("onenote_graph_import.requests.get")
    def test_a_single_match_is_returned(self, mock_get):
        mock_get.return_value = _response(
            {"value": [{"id": "sec-1", "displayName": "Fast 800 Program"}]}
        )
        section = ogi.find_section("token", "Fast 800 Program")
        self.assertEqual(section["id"], "sec-1")

    @patch("onenote_graph_import.requests.get")
    def test_the_filter_escapes_an_apostrophe_in_the_name(self, mock_get):
        mock_get.return_value = _response({"value": [{"id": "sec-1"}]})
        ogi.find_section("token", "Nonna's Recipes")
        params = mock_get.call_args.kwargs["params"]
        self.assertIn("Nonna''s Recipes", params["$filter"])

    @patch("onenote_graph_import.requests.get")
    def test_no_match_raises(self, mock_get):
        mock_get.return_value = _response({"value": []})
        with self.assertRaises(RuntimeError):
            ogi.find_section("token", "Nonexistent Section")

    @patch("onenote_graph_import.requests.get")
    def test_more_than_one_match_raises_rather_than_guessing(self, mock_get):
        mock_get.return_value = _response(
            {"value": [{"id": "sec-1"}, {"id": "sec-2"}]}
        )
        with self.assertRaises(RuntimeError):
            ogi.find_section("token", "Fast 800 Program")


class TestListPages(unittest.TestCase):
    @patch("onenote_graph_import.requests.get")
    def test_pagination_follows_odata_next_link(self, mock_get):
        first = _response(
            {
                "value": [{"id": "p1", "title": "A"}],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/next-page",
            }
        )
        second = _response({"value": [{"id": "p2", "title": "B"}]})
        mock_get.side_effect = [first, second]

        pages = ogi.list_pages("token", "sec-1")
        self.assertEqual([p["id"] for p in pages], ["p1", "p2"])
        self.assertEqual(mock_get.call_count, 2)
        # The nextLink already carries the full query string, so the second
        # call must not re-attach $select/$top on top of it.
        self.assertIsNone(mock_get.call_args_list[1].kwargs["params"])

    @patch("onenote_graph_import.requests.get")
    def test_a_single_page_of_results_needs_one_call(self, mock_get):
        mock_get.return_value = _response({"value": [{"id": "p1", "title": "A"}]})
        pages = ogi.list_pages("token", "sec-1")
        self.assertEqual(len(pages), 1)
        self.assertEqual(mock_get.call_count, 1)


class TestFetchSectionPages(unittest.TestCase):
    @patch("onenote_graph_import.page_content_text")
    @patch("onenote_graph_import.list_pages")
    @patch("onenote_graph_import.find_section")
    def test_pages_come_back_as_title_body_pairs(self, mock_find, mock_list, mock_content):
        mock_find.return_value = {"id": "sec-1", "displayName": "Fast 800 Program"}
        mock_list.return_value = [
            {"id": "p1", "title": "Apple Pear and Hazelnut Yogurt"},
            {"id": "p2", "title": "Green Curry"},
        ]
        mock_content.side_effect = ["yogurt, apple, pear", "chicken, coconut milk"]

        pages = ogi.fetch_section_pages("token", "Fast 800 Program")

        self.assertEqual(
            pages,
            [
                {"title": "Apple Pear and Hazelnut Yogurt", "body": "yogurt, apple, pear"},
                {"title": "Green Curry", "body": "chicken, coconut milk"},
            ],
        )
        mock_list.assert_called_once_with("token", "sec-1")

    @patch("onenote_graph_import.page_content_text")
    @patch("onenote_graph_import.list_pages")
    @patch("onenote_graph_import.find_section")
    def test_an_untitled_page_falls_back_to_its_id(self, mock_find, mock_list, mock_content):
        """Every progress line in import_pages needs a non-empty title to
        report against."""
        mock_find.return_value = {"id": "sec-1"}
        mock_list.return_value = [{"id": "p1", "title": ""}]
        mock_content.return_value = "body text"

        pages = ogi.fetch_section_pages("token", "Fast 800 Program")
        self.assertEqual(pages[0]["title"], "p1")


class TestAcquireToken(unittest.TestCase):
    def test_a_missing_client_id_is_a_clear_error(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ONENOTE_CLIENT_ID", None)
            with self.assertRaises(RuntimeError) as ctx:
                ogi._client_id()
            self.assertIn("ONENOTE_CLIENT_ID", str(ctx.exception))

    @patch("onenote_graph_import.msal.PublicClientApplication")
    def test_an_existing_account_renews_silently_with_no_device_prompt(self, mock_app_cls):
        app = MagicMock()
        app.get_accounts.return_value = [{"username": "me@example.com"}]
        app.acquire_token_silent.return_value = {"access_token": "tok-123"}
        mock_app_cls.return_value = app

        with patch.dict(os.environ, {"ONENOTE_CLIENT_ID": "client-id"}):
            token = ogi.acquire_token()

        self.assertEqual(token, "tok-123")
        app.initiate_device_flow.assert_not_called()

    @patch("onenote_graph_import.msal.PublicClientApplication")
    def test_no_cached_account_falls_back_to_the_device_flow(self, mock_app_cls):
        app = MagicMock()
        app.get_accounts.return_value = []
        app.initiate_device_flow.return_value = {
            "user_code": "ABCD-1234",
            "message": "Go to https://microsoft.com/devicelogin and enter ABCD-1234",
        }
        app.acquire_token_by_device_flow.return_value = {"access_token": "tok-456"}
        mock_app_cls.return_value = app

        with patch.dict(os.environ, {"ONENOTE_CLIENT_ID": "client-id"}):
            token = ogi.acquire_token()

        self.assertEqual(token, "tok-456")
        app.initiate_device_flow.assert_called_once()

    @patch("onenote_graph_import.msal.PublicClientApplication")
    def test_a_result_with_no_access_token_raises(self, mock_app_cls):
        app = MagicMock()
        app.get_accounts.return_value = []
        app.initiate_device_flow.return_value = {"user_code": "x", "message": "go here"}
        app.acquire_token_by_device_flow.return_value = {
            "error": "authorization_declined",
            "error_description": "The user declined the sign in.",
        }
        mock_app_cls.return_value = app

        with patch.dict(os.environ, {"ONENOTE_CLIENT_ID": "client-id"}):
            with self.assertRaises(RuntimeError) as ctx:
                ogi.acquire_token()
        self.assertIn("declined", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

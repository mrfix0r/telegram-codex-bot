import json
import ssl
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from internal_bot.telegram import TelegramClient, TelegramConnectionError


class FakeResponse:
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"ok": true, "result": []}'


class TelegramClientTests(unittest.TestCase):
    @patch("internal_bot.telegram.time.sleep")
    @patch("internal_bot.telegram.urlopen")
    def test_get_updates_retries_temporary_ssl_failure(
        self,
        mocked_urlopen: Mock,
        mocked_sleep: Mock,
    ) -> None:
        mocked_urlopen.side_effect = [
            URLError(ssl.SSLError("unexpected EOF")),
            FakeResponse(),
        ]
        client = TelegramClient("test-token")

        self.assertEqual(client.get_updates(None, 30), [])
        self.assertEqual(mocked_urlopen.call_count, 2)
        mocked_sleep.assert_called_once_with(1)

    @patch("internal_bot.telegram.urlopen")
    def test_send_message_does_not_retry_ambiguous_failure(
        self,
        mocked_urlopen: Mock,
    ) -> None:
        mocked_urlopen.side_effect = URLError(ssl.SSLError("unexpected EOF"))
        client = TelegramClient("test-token")

        with self.assertRaises(TelegramConnectionError):
            client.send_message(10, "Ответ")

        mocked_urlopen.assert_called_once()

    @patch("internal_bot.telegram.urlopen", return_value=FakeResponse())
    def test_get_updates_requests_callback_queries(self, mocked_urlopen: Mock) -> None:
        TelegramClient("test-token").get_updates(None, 30)

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["allowed_updates"], ["message", "callback_query"])

    @patch("internal_bot.telegram.urlopen", return_value=FakeResponse())
    def test_send_message_attaches_inline_keyboard(self, mocked_urlopen: Mock) -> None:
        markup = {
            "inline_keyboard": [[{"text": "Статус", "callback_data": "show:status"}]]
        }
        TelegramClient("test-token").send_message(10, "Меню", reply_markup=markup)

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["reply_markup"], markup)


if __name__ == "__main__":
    unittest.main()

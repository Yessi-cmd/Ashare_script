import unittest

from notifier import _split_message


class NotifierTests(unittest.TestCase):
    def test_split_message_respects_limit_and_preserves_text(self):
        message = "第一行\n" + "a" * 30 + "\n最后一行"
        chunks = _split_message(message, limit=12)
        self.assertTrue(all(len(chunk) <= 12 for chunk in chunks))
        self.assertEqual("".join(chunks), message)


if __name__ == "__main__":
    unittest.main()

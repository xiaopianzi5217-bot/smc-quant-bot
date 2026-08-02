import os
import tempfile
import unittest

from notifier import telegram


class NotifierPushplusTests(unittest.TestCase):
    def test_resolve_wechat_token_accepts_wx_bot_key(self):
        os.environ.pop("PUSHPLUS_TOKEN", None)
        os.environ.pop("WX_BOT_KEY", None)
        os.environ.pop("WX_TOKEN", None)
        os.environ.pop("WEIXIN_BOT_KEY", None)
        os.environ.pop("WECHAT_BOT_KEY", None)
        os.environ["WX_BOT_KEY"] = "alias-token"

        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = os.path.join(tmpdir, "pushplus_token.txt")
            with open(token_file, "w", encoding="utf-8") as fh:
                fh.write("")
            self.assertEqual(telegram._resolve_wechat_token(telegram.Path(token_file)), "alias-token")


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import AsyncMock, patch

from server.services.http_clients import multimodal_client_lifespan


class MultimodalClientLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_is_closed_when_lifespan_body_raises(self):
        with patch("server.services.http_clients.close_multimodal_client", new=AsyncMock()) as close_client:
            with self.assertRaisesRegex(RuntimeError, "boom"):
                async with multimodal_client_lifespan(None):
                    raise RuntimeError("boom")

        close_client.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()

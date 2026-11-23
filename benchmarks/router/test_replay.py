"""Mock checks for stream timing, classification, and windowed scoring."""
import unittest

import httpx

from prompts import qa_specs
from replay import ENGINES, HITS, QUERIES, cache_ratio, summarize
from transport import send_one


class Replays(unittest.IsolatedAsyncioTestCase):
    async def send(self, stream):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=stream))
        async with httpx.AsyncClient(transport=transport) as client:
            return await send_one(client, qa_specs(1, "mock")[0])

    async def test_complete_stream(self):
        row = await self.send('data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
                              'data: {"choices":[{"delta":{"content":"391"},"finish_reason":"stop"}]}\n\n'
                              'data: [DONE]\n\n')
        self.assertEqual(row["state"], "success")
        self.assertLessEqual(row["started"], row["first_at"])
        self.assertLessEqual(row["first_at"], row["ended"])
        self.assertLessEqual(row["ttft_ms"], row["complete_ms"])

    async def test_incomplete_stream(self):
        row = await self.send('data: {"choices":[{"delta":{"content":"391"}}]}\n\n')
        self.assertEqual(row["state"], "stream_interrupted")

    async def test_truncation(self):
        row = await self.send('data: {"choices":[{"delta":{"content":"391"},"finish_reason":"length"}]}\n\n'
                              'data: [DONE]\n\n')
        self.assertEqual(row["state"], "truncated")

    def test_cache_reset(self):
        keys = list(ENGINES)
        def sample(a, b):
            return {"engines": {key: {HITS: pair[0], QUERIES: pair[1]}
                                for key, pair in zip(keys, (a, b))}}
        samples = [sample((1, 2), (10, 20)), sample((3, 6), (1, 2)), sample((4, 8), (4, 12))]
        result = cache_ratio(samples)
        self.assertEqual((result["hits"], result["queries"], result["intervals"]), (6, 16, 3))

    def test_quality_and_window(self):
        base = dict(state="success", ttft_ms=1, complete_ms=2, budget_ms=5, completion_budget_ms=3)
        result = summarize([dict(base, started=1), dict(base, started=11),
                            dict(base, state="quality_failure", started=2)], 0, 10)
        self.assertEqual(result["complete_qualified"], 2)
        self.assertEqual(result["complete_qualified_rps"], 0.1)


if __name__ == "__main__":
    unittest.main()

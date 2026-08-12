import json
import os
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.clients import OllamaClient, OllamaOutputLimitError, QdrantClient
from app.config import load_config
from app.openai_compat import sse_chunk
from app.models import ChatCompletionRequest, ChatMessage, QueryChunk
from app.query import PreparedChat, QueryEngine


SOURCE_ONE = QueryChunk(
    text="Alpha text",
    source_url="https://example.com/alpha",
    source_title="Alpha",
    score=0.9,
)
SOURCE_TWO = QueryChunk(
    text="Beta text",
    source_url="https://example.com/beta",
    source_title="Beta",
    score=0.8,
)


class FakeOllama:
    model = "qwen2.5:7b"

    def __init__(self):
        self.generate_calls = []
        self.embedded = []

    async def generate(self, prompt, system=None, max_tokens=1024):
        self.generate_calls.append((prompt, system, max_tokens))
        return "standalone follow-up"

    async def embed(self, text):
        self.embedded.append(text)
        return [0.0] * 768

    async def chat_stream(self, messages, **kwargs):
        yield "answer"
        yield " streamed"


class FakeQdrant:
    def __init__(self, hits=None):
        self.hits = hits or []

    def search(self, vector, top_k=5, filters=None):
        return self.hits[:top_k]


class QueryEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_standalone_question_skips_rewrite(self):
        ollama = FakeOllama()
        engine = QueryEngine(ollama, FakeQdrant([SOURCE_ONE.model_dump()]))

        prepared = await engine.prepare_chat(
            [ChatMessage(role="user", content="What is alpha?")]
        )

        self.assertEqual(prepared.query, "What is alpha?")
        self.assertEqual(ollama.generate_calls, [])
        self.assertEqual(ollama.embedded, ["What is alpha?"])
        self.assertEqual(prepared.sources, [SOURCE_ONE])

    async def test_follow_up_rewrites_before_retrieval(self):
        ollama = FakeOllama()
        engine = QueryEngine(ollama, FakeQdrant([SOURCE_ONE.model_dump()]))

        prepared = await engine.prepare_chat(
            [
                ChatMessage(role="user", content="Tell me about alpha."),
                ChatMessage(role="assistant", content="Alpha is documented."),
                ChatMessage(role="user", content="How does it compare?"),
            ]
        )

        self.assertEqual(prepared.query, "standalone follow-up")
        self.assertEqual(ollama.embedded, ["standalone follow-up"])
        self.assertEqual(ollama.generate_calls[0][2], 128)
        self.assertIn("How does it compare?", ollama.generate_calls[0][0])

    async def test_stream_and_sources_preserve_order_and_duplicates(self):
        engine = QueryEngine(FakeOllama(), FakeQdrant())
        prepared = PreparedChat(
            query="query",
            sources=[SOURCE_ONE, SOURCE_TWO, SOURCE_ONE],
            messages=[{"role": "user", "content": "query"}],
            timings={},
        )

        answer = "".join(
            [
                token
                async for token in engine.stream_prepared_chat(
                    prepared,
                    max_tokens=100,
                    temperature=0.2,
                    top_p=0.9,
                    stop=None,
                )
            ]
        )
        sources = engine.format_sources(prepared.sources)

        self.assertEqual(answer, "answer streamed")
        self.assertLess(sources.index("Alpha"), sources.index("Beta"))
        self.assertEqual(sources.count("https://example.com/alpha"), 2)

    async def test_empty_retrieval_does_not_call_generation(self):
        ollama = FakeOllama()
        engine = QueryEngine(ollama, FakeQdrant())
        prepared = await engine.prepare_chat([ChatMessage(role="user", content="Unknown")])
        answer = "".join(
            [
                token
                async for token in engine.stream_prepared_chat(
                    prepared,
                    max_tokens=100,
                    temperature=0.2,
                    top_p=0.9,
                    stop=None,
                )
            ]
        )
        self.assertEqual(answer, "No relevant information found in the knowledge base.")
        self.assertEqual(engine.format_sources([]), "")


class ProtocolTests(unittest.TestCase):
    def test_open_webui_tools_are_accepted(self):
        request = ChatCompletionRequest(**{
            "model": "research-corpus",
            "messages": [{"role": "user", "content": "What is indexed?"}],
            "stream": True,
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_current_timestamp",
                    "description": "Get the current timestamp.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        })

        self.assertEqual(request.tools[0]["function"]["name"], "get_current_timestamp")

    def test_sse_chunk_is_openai_compatible(self):
        chunk = sse_chunk("chatcmpl-test", 123, {"content": "hello"})
        self.assertTrue(chunk.startswith("data: "))
        payload = json.loads(chunk.removeprefix("data: ").strip())
        self.assertEqual(payload["object"], "chat.completion.chunk")
        self.assertEqual(payload["choices"][0]["delta"]["content"], "hello")


class ConfigurationTests(unittest.TestCase):
    def test_generation_model_default_matches_deployment_default(self):
        with patch.dict(os.environ, {"MINIMAX_SUBSCRIPTION_KEY": "test-key"}, clear=True):
            config = load_config()

        self.assertEqual(config.llm_model, "qwen3.5:9b")
        self.assertEqual(config.answer_reserve_tokens, 2048)


class OllamaThinkingModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_disables_thinking_so_answer_is_returned(self):
        response = MagicMock()
        response.json.return_value = {"response": "ANSWER=42"}
        client = object.__new__(OllamaClient)
        client.base_url = "http://ollama:11434"
        client.model = "qwen3.5:9b"
        client._client = MagicMock(post=AsyncMock(return_value=response))

        answer = await client.generate("Return exactly ANSWER=42")

        self.assertEqual(answer, "ANSWER=42")
        payload = client._client.post.await_args.kwargs["json"]
        self.assertIs(payload["think"], False)

    async def test_generate_reports_output_limit_instead_of_returning_partial_json(self):
        response = MagicMock()
        response.json.return_value = {
            "response": '{"key_findings":[',
            "done_reason": "length",
            "eval_count": 1024,
            "truncated": False,
        }
        client = object.__new__(OllamaClient)
        client.base_url = "http://ollama:11434"
        client.model = "qwen3.5:9b"
        client._client = MagicMock(post=AsyncMock(return_value=response))

        with self.assertRaisesRegex(
            OllamaOutputLimitError, "1024-token output limit.*done_reason=length"
        ) as raised:
            await client.generate("Return JSON", max_tokens=1024, json_mode=True)
        self.assertEqual(raised.exception.diagnostic["completion_tokens"], 1024)
        self.assertEqual(raised.exception.diagnostic["response_preview"], '{"key_findings":[')
        self.assertEqual(len(raised.exception.diagnostic["response_sha256"]), 64)

    async def test_generate_rejects_prompt_truncation(self):
        response = MagicMock()
        response.json.return_value = {
            "response": "{}", "done_reason": "stop", "truncated": True,
        }
        client = object.__new__(OllamaClient)
        client.base_url = "http://ollama:11434"
        client.model = "qwen3.5:9b"
        client._client = MagicMock(post=AsyncMock(return_value=response))

        with self.assertRaisesRegex(RuntimeError, "truncated the input prompt"):
            await client.generate("Return JSON", json_mode=True)

    async def test_generate_returns_longer_completed_structured_output(self):
        body = '{"value":"' + "x" * 5000 + '"}'
        response = MagicMock()
        response.json.return_value = {
            "response": body,
            "done_reason": "stop",
            "eval_count": 1300,
            "truncated": False,
        }
        client = object.__new__(OllamaClient)
        client.base_url = "http://ollama:11434"
        client.model = "qwen3.5:9b"
        client._client = MagicMock(post=AsyncMock(return_value=response))

        self.assertEqual(
            await client.generate("Return JSON", max_tokens=2048, json_mode=True), body
        )
        self.assertEqual(
            client._client.post.await_args.kwargs["json"]["options"]["num_predict"],
            2048,
        )

    async def test_chat_stream_disables_thinking_so_content_is_streamed(self):
        response = MagicMock()
        response.raise_for_status = MagicMock()

        async def lines():
            yield json.dumps({"message": {"content": "ANSWER=42"}, "done": False})
            yield json.dumps({"message": {"content": ""}, "done": True})

        response.aiter_lines = lines
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        client = object.__new__(OllamaClient)
        client.base_url = "http://ollama:11434"
        client.model = "qwen3.5:9b"
        client._client = MagicMock()
        client._client.stream.return_value = context

        answer = "".join([
            token async for token in client.chat_stream([
                {"role": "user", "content": "Return exactly ANSWER=42"}
            ])
        ])

        self.assertEqual(answer, "ANSWER=42")
        payload = client._client.stream.call_args.kwargs["json"]
        self.assertIs(payload["think"], False)


class QdrantInitializationTests(unittest.TestCase):
    def test_existing_collection_is_not_recreated(self):
        client_type = MagicMock()
        client = client_type.return_value
        client.collection_exists.return_value = True
        client.get_collection.return_value = SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(
                    vectors=SimpleNamespace(size=768, distance="Cosine")
                )
            )
        )
        qdrant_module = ModuleType("qdrant_client")
        qdrant_module.QdrantClient = client_type
        models_module = ModuleType("qdrant_client.models")
        models_module.Distance = SimpleNamespace(COSINE="Cosine")
        models_module.VectorParams = MagicMock()

        with patch.dict(
            sys.modules,
            {"qdrant_client": qdrant_module, "qdrant_client.models": models_module},
        ):
            QdrantClient("http://qdrant:6333", "research_corpus")

        client.create_collection.assert_not_called()
        self.assertFalse(hasattr(client, "recreate_collection") and client.recreate_collection.called)


if __name__ == "__main__":
    unittest.main()

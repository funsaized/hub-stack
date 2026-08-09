"""Regression tests for safe Qdrant collection initialization."""

import unittest

from qdrant_client import QdrantClient as RawQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.clients import CollectionConfigurationError, QdrantClient


class QdrantCollectionInitializationTests(unittest.TestCase):
    def setUp(self):
        self.backend = RawQdrantClient(":memory:")

    def tearDown(self):
        self.backend.close()

    def make_client(self, *, vector_size=768, distance=Distance.COSINE):
        return QdrantClient(
            "http://unused",
            "research_corpus",
            vector_size=vector_size,
            distance=distance,
            embedding_model="nomic-embed-text",
            client=self.backend,
        )

    def insert_sentinel(self):
        self.backend.upsert(
            "research_corpus",
            points=[
                PointStruct(
                    id="00000000-0000-0000-0000-000000000001",
                    vector=[0.001] * 768,
                    payload={"text": "must survive"},
                )
            ],
            wait=True,
        )

    def assert_sentinel_exists(self):
        points = self.backend.retrieve(
            "research_corpus",
            ids=["00000000-0000-0000-0000-000000000001"],
        )
        self.assertEqual(points[0].payload["text"], "must survive")

    def test_missing_collection_is_created_once(self):
        self.make_client()
        self.make_client()

        collections = self.backend.get_collections().collections
        self.assertEqual([item.name for item in collections], ["research_corpus"])

    def test_existing_collection_and_sentinel_survive_restart(self):
        self.make_client()
        self.insert_sentinel()

        self.make_client()

        self.assert_sentinel_exists()

    def test_incompatible_collection_fails_without_deleting_data(self):
        for incompatible in (
            {"vector_size": 384, "distance": Distance.COSINE},
            {"vector_size": 768, "distance": Distance.DOT},
        ):
            with self.subTest(**incompatible):
                self.make_client()
                self.insert_sentinel()

                with self.assertRaisesRegex(
                    CollectionConfigurationError,
                    r"migration required.*research_corpus.*nomic-embed-text",
                ):
                    self.make_client(**incompatible)

                self.assert_sentinel_exists()
                self.backend.delete_collection("research_corpus")

    def test_named_vector_collection_fails_without_deleting_data(self):
        self.backend.create_collection(
            "research_corpus",
            vectors_config={
                "dense": VectorParams(size=768, distance=Distance.COSINE),
            },
        )
        self.backend.upsert(
            "research_corpus",
            points=[
                PointStruct(
                    id="00000000-0000-0000-0000-000000000001",
                    vector={"dense": [0.001] * 768},
                    payload={"text": "must survive"},
                )
            ],
            wait=True,
        )

        with self.assertRaisesRegex(
            CollectionConfigurationError,
            r"migration required.*named vectors.*dense",
        ):
            self.make_client()

        points = self.backend.retrieve(
            "research_corpus",
            ids=["00000000-0000-0000-0000-000000000001"],
        )
        self.assertEqual(points[0].payload["text"], "must survive")


if __name__ == "__main__":
    unittest.main()

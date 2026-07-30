"""
Dense hippocampal recall: episodes must be findable from paraphrased
queries that share no keywords with the stored text.
"""

from bio_agent_os.memory.episodes import EpisodeStore

STORAGE = "test_data"


class FakeEmbedder:
    """Maps known concepts onto fixed axes so cosine behaves predictably."""

    VOCAB = {
        "canine": [1.0, 0.0, 0.0],
        "puppy": [0.95, 0.05, 0.0],
        "dog": [0.9, 0.1, 0.0],
        "pottery": [0.0, 1.0, 0.0],
        "vase": [0.0, 0.9, 0.1],
        "weather": [0.0, 0.0, 1.0],
    }

    def embed(self, text: str):
        vector = [0.0, 0.0, 0.0]
        hits = 0
        for token, axis in self.VOCAB.items():
            if token in text.lower():
                vector = [a + b for a, b in zip(vector, axis)]
                hits += 1
        if not hits:
            return [0.0, 0.0, 0.0]
        norm = sum(value * value for value in vector) ** 0.5 or 1.0
        return [value / norm for value in vector]


def test_dense_recall_matches_paraphrase_without_keyword_overlap():
    store = EpisodeStore(agent_name="dense-test", storage_dir=STORAGE, embedder=FakeEmbedder())
    store.add(raw_payload="I adopted the sweetest puppy yesterday!", workspace_id="ws-d")
    store.add(raw_payload="My pottery class made a vase.", workspace_id="ws-d")
    store.add(raw_payload="The weather is windy today.", workspace_id="ws-d")

    # Writes skip embedding to keep the write path off the network; vectors are
    # materialized just after the response (API background task) or at sleep.
    # Run that step explicitly here — dense recall is delayed, never lost.
    assert store.backfill_vectors() == 3

    # "canine" shares zero tokens with "puppy" — only dense similarity links them.
    results = store.search_text("canine", limit=2, workspace_id="ws-d")
    assert results
    assert "puppy" in results[0]["raw_payload"]
    assert all("vector" not in key for result in results for key in result)


def test_keyword_search_still_works_without_embedder():
    store = EpisodeStore(agent_name="keyword-test", storage_dir=STORAGE)
    store.add(raw_payload="Deployment failed on the staging cluster.", workspace_id="ws-k")
    results = store.search_text("staging deployment", limit=3, workspace_id="ws-k")
    assert results
    assert "staging" in results[0]["raw_payload"]

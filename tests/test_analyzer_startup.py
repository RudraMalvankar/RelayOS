from pipeline.analyzer import PromptAnalyzer


def test_analyzer_does_not_load_embeddings_by_default(monkeypatch):
    monkeypatch.setenv("ENABLE_SEMANTIC_CLASSIFIER", "false")
    analyzer = PromptAnalyzer()

    assert analyzer._semantic is None

"""Integration tests for promptry using a real RAG pipeline.

These run against a locally-running Ollama instance + in-process ChromaDB.
Not part of the default pytest run — invoke via `pytest integration_tests/ -m integration`.
"""

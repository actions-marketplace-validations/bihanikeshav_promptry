"""RAG lab — real corpus, real users, real benchmarks.

Self-contained mock production environment for testing promptry against
realistic data:

- 4 public-domain books (Sherlock, Pride & Prejudice, Frankenstein, Federalist)
  vectorised in ChromaDB
- 15 system-prompt variants (terse, verbose, grounded, persona, etc.)
- 2 Ollama models (qwen3:1.7b vs qwen3:4b-thinking)
- 18 user personas, ~30 messages each = ~500 interactions
- Semantic + LLM-as-judge eval suites

Run as:

    python -m mockdb.rag_lab.build      # vectorise corpus + warm cache
    python -m mockdb.rag_lab.simulate   # run users against the RAG pipeline
    python -m mockdb.rag_lab.evaluate   # run eval suites with both judges
    python -m mockdb.rag_lab.report     # print insights
"""

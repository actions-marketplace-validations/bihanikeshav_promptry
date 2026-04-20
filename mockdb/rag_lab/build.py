"""Step 1: vectorise the corpus.

Run as:  python -m mockdb.rag_lab.build
"""
from __future__ import annotations

from mockdb.rag_lab.rag import build_index


def main() -> None:
    print("Building ChromaDB index from public-domain corpus...")
    n = build_index(verbose=True)
    print(f"\nIndexed {n} chunks across 4 books.")
    print("Vector store: mockdb/rag_lab/chroma/")


if __name__ == "__main__":
    main()

"""A small, real RAG pipeline for exercising promptry end-to-end.

- Vector store: ChromaDB in-memory, default ONNX embeddings (no API key).
- Generator: local Ollama via its HTTP API (no SDK needed).
- Corpus: ~20 short facts covering topics a tutor might answer.

The pipeline wraps its system prompt with promptry.track() so every
call produces a prompt-version row, lets us exercise drift/comparison
later. Response generation strips any <think>...</think> blocks so
reasoning-model outputs don't leak into assertions.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

import chromadb

from promptry import track

OLLAMA_URL = "http://localhost:11434"
# Use a small, non-thinking model for fast, predictable output.
# qwen3-thinking eats token budget on reasoning before answering.
DEFAULT_MODEL = "qwen2.5:0.5b"

CORPUS: list[tuple[str, str]] = [
    ("photosynthesis",
     "Photosynthesis converts light energy into chemical energy in chloroplasts. "
     "Plants use carbon dioxide and water to make glucose and release oxygen."),
    ("mitosis",
     "Mitosis is a cell division process that produces two genetically identical daughter cells. "
     "Its four main phases are prophase, metaphase, anaphase, and telophase."),
    ("meiosis",
     "Meiosis produces four haploid gametes from one diploid parent cell through two divisions. "
     "It introduces genetic variation via crossing-over and independent assortment."),
    ("mitochondria",
     "Mitochondria are the energy powerhouses of the cell, producing ATP via oxidative phosphorylation. "
     "They have their own circular DNA and are inherited maternally."),
    ("dna_structure",
     "DNA is a double helix of two antiparallel strands held by hydrogen bonds between base pairs. "
     "Adenine pairs with thymine, and guanine pairs with cytosine."),
    ("recursion",
     "Recursion is when a function calls itself with a smaller input until it hits a base case. "
     "Classic examples include computing factorial and traversing tree structures."),
    ("binary_search",
     "Binary search finds a value in a sorted array in O(log n) time by repeatedly halving the range. "
     "It requires the input to be sorted and random-access."),
    ("hash_table",
     "A hash table stores key-value pairs using a hash function to compute bucket indices. "
     "Average-case lookup is O(1); collisions are handled by chaining or open addressing."),
    ("tcp_handshake",
     "The TCP three-way handshake establishes a connection via SYN, SYN-ACK, and ACK packets. "
     "It synchronizes sequence numbers before data transfer begins."),
    ("http_status_codes",
     "HTTP status codes are three-digit numbers grouped by class. "
     "2xx means success, 3xx means redirection, 4xx means client error, and 5xx means server error."),
    ("rest_principles",
     "REST APIs follow principles like statelessness, uniform interface, and client-server separation. "
     "They typically use HTTP verbs (GET, POST, PUT, DELETE) on resource URIs."),
    ("sql_join",
     "A SQL JOIN combines rows from two or more tables based on a related column. "
     "INNER JOIN keeps only matches; LEFT JOIN keeps all rows from the left table."),
    ("git_rebase",
     "Git rebase rewrites commit history by replaying commits onto a new base. "
     "Unlike merge, it produces a linear history but rewrites commit hashes."),
    ("docker_image",
     "A Docker image is a read-only template with application code, runtime, and dependencies. "
     "Containers are runnable instances of an image with their own writable layer."),
    ("gravity",
     "Gravity is the attractive force between any two masses with magnitude proportional to their masses "
     "and inversely proportional to the square of their distance apart."),
    ("newton_third_law",
     "Newton's third law states that for every action there is an equal and opposite reaction. "
     "Forces always occur in pairs acting on different objects."),
    ("atomic_number",
     "The atomic number of an element is the number of protons in its nucleus. "
     "It uniquely identifies a chemical element on the periodic table."),
    ("french_revolution",
     "The French Revolution began in 1789, driven by financial crisis and Enlightenment ideals. "
     "It ended the absolute monarchy and led to the Napoleonic era."),
    ("ww2_endyear",
     "World War II ended in 1945 with Germany's surrender in May and Japan's surrender in September "
     "following the atomic bombings of Hiroshima and Nagasaki."),
    ("python_gil",
     "Python's Global Interpreter Lock (GIL) allows only one thread to execute Python bytecode at a time. "
     "It simplifies memory management but limits CPU-bound multithreading."),
]

SYSTEM_PROMPT_V1 = (
    "You are a concise tutor. Answer using only the provided context. "
    "If the context does not contain the answer, say you don't know. "
    "Keep answers under two sentences."
)

SYSTEM_PROMPT_V2 = (
    "You are a precise tutor. Answer strictly using the provided context, "
    "citing key terms from it. If the context does not contain the answer, "
    "respond exactly with 'I don't know based on the provided context.' "
    "Keep answers under two sentences."
)


@dataclass
class RAGResponse:
    question: str
    answer: str
    retrieved_ids: list[str]
    raw_llm_output: str


def build_store() -> chromadb.Collection:
    """Build an in-memory ChromaDB collection seeded with the corpus."""
    client = chromadb.EphemeralClient()
    coll = client.get_or_create_collection(name="rag_canary")
    ids = [doc_id for doc_id, _ in CORPUS]
    docs = [text for _, text in CORPUS]
    coll.add(ids=ids, documents=docs)
    return coll


def ollama_generate(
    model: str,
    prompt: str,
    system: str,
    *,
    num_predict: int = 512,
    timeout: float = 120.0,
) -> str:
    payload = json.dumps({
        "model": model,
        "system": system,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": num_predict},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("response", "")


_THINK_BLOCK = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Strip <think>...</think> blocks emitted by reasoning models like qwen3-thinking."""
    return _THINK_BLOCK.sub("", text).strip()


def rag_pipeline(
    question: str,
    coll: chromadb.Collection,
    *,
    model: str = DEFAULT_MODEL,
    system_prompt: str = SYSTEM_PROMPT_V1,
    prompt_name: str = "rag-tutor",
    k: int = 3,
) -> RAGResponse:
    """Retrieve top-k docs, prompt the LLM, strip reasoning, return."""
    tracked_system = track(system_prompt, prompt_name)

    query = coll.query(query_texts=[question], n_results=k)
    doc_ids = query["ids"][0] if query.get("ids") else []
    docs = query["documents"][0] if query.get("documents") else []

    context = "\n\n".join(f"[{i+1}] {d}" for i, d in enumerate(docs))
    user_prompt = f"Context:\n{context}\n\nQuestion: {question}\nAnswer:"

    raw = ollama_generate(model=model, prompt=user_prompt, system=tracked_system)
    answer = strip_reasoning(raw)
    return RAGResponse(
        question=question,
        answer=answer,
        retrieved_ids=doc_ids,
        raw_llm_output=raw,
    )


def ollama_judge_factory(model: str = DEFAULT_MODEL) -> Any:
    """A judge function for promptry.set_judge() that uses Ollama."""
    def _judge(prompt: str) -> str:
        raw = ollama_generate(
            model=model,
            system=(
                "You are a strict grader. Respond with ONLY a single JSON object "
                'of the form {"score": <float 0-1>, "reason": "<short>"}. '
                "Do not wrap in code fences. No preamble, no explanation outside JSON."
            ),
            prompt=prompt,
            num_predict=256,
        )
        return strip_reasoning(raw).strip()
    return _judge


def ollama_available() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2.0) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False

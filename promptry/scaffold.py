"""Template content and file-writing logic for `promptry init`.

Kept separate from cli.py so the command body is just parse -> call -> render:
this module owns *what* gets written, the CLI owns *how it's reported*.
"""
from __future__ import annotations

from pathlib import Path

CONFIG_TEMPLATE = (
    '# promptry config\n'
    '# docs: https://promptry.meownikov.xyz\n'
    '\n'
    '[storage]\n'
    '# db_path = "~/.promptry/promptry.db"\n'
    '# mode = "sync"                # sync | async | off\n'
    '\n'
    '[tracking]\n'
    '# sample_rate = 1.0            # fraction of track() calls that write (1.0 = all)\n'
    '# context_sample_rate = 0.1    # fraction of track_context() calls (lower in prod)\n'
    '\n'
    '[notifications]\n'
    '# webhook_url = "https://hooks.slack.com/services/..."\n'
    '# email = "you@example.com"\n'
    '\n'
    '[monitor]\n'
    '# interval_minutes = 1440   # how often to run (daily)\n'
    '# threshold = 0.05          # flag if score drops more than 5%\n'
    '# window = 30               # number of recent runs to look at\n'
    '\n'
    '# --- Team / project sections (shared via git; API keys NEVER go here --\n'
    '# they live in env vars, read by litellm; only key presence is reported) --\n'
    '\n'
    '[dashboard]\n'
    '# default_days = 14           # default time window in the dashboard\n'
    '\n'
    '[judge]\n'
    '# model = "gpt-4o-mini"        # LLM-judge model id for llm_judge / assert_semantic assertions\n'
    '# max_prompt_chars = 8000      # cap judge-prompt size (token-spend guard); 0 = off\n'
    '\n'
    '[slo]                          # CI fails the run if a latency budget is breached\n'
    '# max_latency_ms = 8000        # no single test slower than this (0 = not enforced)\n'
    '# p95_latency_ms = 5000        # 95th-percentile test latency (0 = not enforced)\n'
    '\n'
    '# Model list shown in the dashboard Playground (repeat the block per model):\n'
    '# [[models]]\n'
    '# id = "gpt-4o-mini"\n'
    '# provider = "openai"\n'
    '# label = "GPT-4o mini"\n'
    '\n'
    '# Pricing overrides — $ per 1M tokens, fills a coverage gap in the rate table:\n'
    '# [pricing.my-custom-model]\n'
    '# in = 1.0\n'
    '# cached = 0.5\n'
    '# cache_write = 1.0\n'
    '# out = 2.0\n'
)

_EXAMPLE_EVAL = '''"""Example eval suite for promptry."""
from promptry import (
    suite,
    assert_semantic,
    assert_contains,
    assert_matches,
    assert_json_valid,
    assert_schema,
)


# ---------------------------------------------------------------------------
# Wire this up to your real LLM/RAG system.  Every suite below -- and every
# wrapper pipeline further down -- routes through this one function, so
# hooking it up once makes ALL suites runnable.
#
# Working example using litellm (a core promptry dependency, so this just
# works once you set an API key -- see the doctor command and the README):
#
#   from litellm import completion
#
#   def my_pipeline(prompt: str) -> str:
#       response = completion(
#           model="gpt-4o-mini",  # or "claude-3-5-sonnet-20241022", "gemini/gemini-1.5-flash", ...
#           messages=[{"role": "user", "content": prompt}],
#       )
#       return response.choices[0].message.content
# ---------------------------------------------------------------------------

def my_pipeline(prompt: str) -> str:
    """Replace this with a call to your real LLM/RAG system -- see the example above."""
    raise NotImplementedError(
        "Replace my_pipeline with a call to your LLM/RAG system — see the comment above."
    )


def my_rag_pipeline(question: str) -> str:
    """RAG pipeline: retrieve context, then generate.  Replace with yours."""
    # e.g. context = retriever.search(question)
    #      return my_pipeline(f"Context: {context}\\n\\nQuestion: {question}")
    return my_pipeline(question)


def my_classifier(text: str) -> str:
    """Classification pipeline.  Should return a single label.  Replace with yours."""
    return my_pipeline(f"Classify the sentiment of this text as positive, negative, or neutral: {text}")


def my_chat_pipeline(message: str) -> str:
    """Conversational AI pipeline.  Replace with your chatbot / assistant call."""
    return my_pipeline(message)


def my_extraction_pipeline(document: str) -> str:
    """Document extraction pipeline.  Should return a JSON string.  Replace with yours."""
    return my_pipeline(f"Extract structured data as JSON (name, email, amount) from: {document}")


def my_summarizer(text: str) -> str:
    """Summarization pipeline.  Replace with your summarization call."""
    return my_pipeline(f"Summarize the following text: {text}")


# ---------------------------------------------------------------------------
# Suite 1 -- smoke-test
# Basic sanity check that your pipeline returns something reasonable.
# ---------------------------------------------------------------------------

@suite("smoke-test")
def test_basic_quality():
    """Basic sanity check that your pipeline returns something reasonable."""
    response = my_pipeline("What is machine learning?")
    assert_semantic(response, "An explanation of machine learning concepts")


# ---------------------------------------------------------------------------
# Suite 2 -- rag-qa
# Evaluate a retrieval-augmented generation pipeline.
# Uses assert_semantic for answer quality and assert_contains to verify
# that key facts appear in the response.
# ---------------------------------------------------------------------------

@suite("rag-qa")
def test_rag_quality():
    """Check that the RAG pipeline returns relevant, factual answers."""
    response = my_rag_pipeline("What is machine learning?")

    # The answer should be semantically close to a good reference answer.
    assert_semantic(response, "Machine learning is a branch of AI that learns from data")

    # The answer must mention these key terms.
    assert_contains(response, ["machine learning", "artificial intelligence"])


# ---------------------------------------------------------------------------
# Suite 3 -- classification
# Verify that a classifier returns well-formed labels.
# Uses assert_matches to enforce the expected output format.
# ---------------------------------------------------------------------------

@suite("classification")
def test_classification_format():
    """Ensure the classifier returns a valid label."""
    label = my_classifier("I love this product!")

    # The label must be exactly one of the allowed values.
    assert_matches(label, r"(positive|negative|neutral)")


# ---------------------------------------------------------------------------
# Suite 4 -- chat-quality
# Evaluate conversational AI for tone and helpfulness.
# Uses assert_semantic for overall helpfulness and assert_contains to
# verify the response has a friendly, supportive tone.
# ---------------------------------------------------------------------------

@suite("chat-quality")
def test_chat_quality():
    """Ensure the chatbot responds helpfully with an appropriate tone."""
    response = my_chat_pipeline("Can you help me reset my password?")

    # The response should be semantically helpful and address the request.
    assert_semantic(response, "A helpful response guiding the user through password reset")

    # The response should contain polite, helpful language.
    assert_contains(response, ["help"])


# ---------------------------------------------------------------------------
# Suite 5 -- extraction
# Evaluate document extraction pipelines for structured output.
# Uses assert_json_valid to ensure the output is well-formed JSON,
# and assert_schema to verify it matches the expected structure.
# ---------------------------------------------------------------------------

@suite("extraction")
def test_extraction_format():
    """Ensure the extraction pipeline returns valid, well-structured JSON."""
    document = "Invoice from Jane Doe (jane@example.com) for $99.99."
    response = my_extraction_pipeline(document)

    # The output must be valid JSON.
    assert_json_valid(response)

    # The JSON must conform to the expected schema.
    assert_schema(response, {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "email": {"type": "string"},
            "amount": {"type": "number"},
        },
        "required": ["name", "email", "amount"],
    })


# ---------------------------------------------------------------------------
# Suite 6 -- summarization
# Evaluate summarization quality for key-point coverage and relevance.
# Uses assert_semantic to check the summary captures the main idea,
# and assert_contains to verify key points are mentioned.
# ---------------------------------------------------------------------------

@suite("summarization")
def test_summarization_quality():
    """Ensure the summarizer captures key points accurately."""
    article = (
        "Artificial intelligence is transforming healthcare by enabling faster "
        "diagnosis, personalized treatment plans, and drug discovery. Researchers "
        "at major hospitals are using AI to detect diseases from medical imaging "
        "with higher accuracy than traditional methods."
    )
    response = my_summarizer(article)

    # The summary should capture the main theme of the article.
    assert_semantic(response, "AI is improving healthcare through better diagnosis and treatment")

    # Key concepts from the article should appear in the summary.
    assert_contains(response, ["artificial intelligence", "healthcare"])


# ---------------------------------------------------------------------------
# Safety testing pipeline -- used by  promptry templates run --module evals
# ---------------------------------------------------------------------------
def pipeline(prompt: str) -> str:
    return my_pipeline(prompt)
'''

_EXAMPLE_EVAL_YAML = '''# Declarative eval suites -- no Python required.
# docs: https://promptry.meownikov.xyz
#
# Uncomment and edit. Run with:  promptry run rag-quality --module evals.yaml
# (Or just `promptry run rag-quality` -- when evals.py is absent, promptry
# auto-discovers evals.yaml / promptry.yaml in the current directory.)
#
# suites:
#   - name: rag-quality
#     # EITHER call your own pipeline (a "module:function" that takes the
#     # case input and returns the model output):
#     pipeline: mymodule:my_pipeline
#     # ...OR drop `pipeline` and let promptry call a model directly:
#     # model: gpt-4o-mini            # routed through promptry.llm.complete
#     # prompt: "Answer concisely: {input}"   # {input} is substituted per case
#     cases:
#       - input: "What is our refund policy?"
#         expect:
#           - contains: "30 days"                 # str or [list, of, str]
#           - not_contains: "lawsuit"
#           - regex: "(refund|return)"            # or {pattern, fullmatch: false}
#           - semantic: {expected: "Refunds within 30 days", threshold: 0.75}
#           - json_valid: true
#           - schema:                             # a JSON schema
#               type: object
#               properties: {amount: {type: number}}
#               required: [amount]
#           - llm: "Is the answer grounded and polite?"   # needs a [judge] model
#           - grounded: {source: "Refunds allowed within 30 days.", threshold: 0.8}
#           # deterministic ($0) checks:
#           - exact: "yes"                        # or {expected, case_sensitive}
#           - levenshtein: {expected: "30 days", min_ratio: 0.8}
#           - rouge_l: {expected: "refund within 30 days", min_score: 0.5}
#           - embedding_distance: {expected: "30 day refunds", max_distance: 0.3}
'''

# Files `promptry init` scaffolds, in the order they're created/reported.
SCAFFOLD_FILES = {
    "promptry.toml": CONFIG_TEMPLATE,
    "evals.py": _EXAMPLE_EVAL,
    "evals.yaml": _EXAMPLE_EVAL_YAML,
}


def scaffold_project(cwd: Path) -> list[tuple[str, bool]]:
    """Write the promptry.toml / evals.py / evals.yaml scaffold files into
    `cwd`, skipping any that already exist.

    Returns a list of (filename, created) pairs in SCAFFOLD_FILES order, so
    the caller can render "created" vs "already exists, skipping" messages.
    """
    results = []
    for filename, content in SCAFFOLD_FILES.items():
        path = cwd / filename
        if path.exists():
            results.append((filename, False))
        else:
            path.write_text(content, encoding="utf-8")
            results.append((filename, True))
    return results

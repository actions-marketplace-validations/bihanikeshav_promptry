"""Wire-contract test: everything RemoteStorage ships must validate against
docs/wire-schema/events.schema.json.

The schema is the single source of truth shared with the JS client
(promptry-js). If Python starts emitting a new field or event type, this
test fails until the schema (and, by extension, the JS side) is updated.
"""
import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

from promptry.config import reset_config
from promptry.storage import reset_storage
from promptry.storage.remote import RemoteStorage, TelemetryEvent


SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs" / "wire-schema" / "events.schema.json"
)


@pytest.fixture(scope="module")
def schema():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def validator(schema):
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPTRY_DB", str(tmp_path / "test.db"))
    reset_config()
    reset_storage()
    yield
    reset_storage()
    reset_config()


class IngestHandler(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        IngestHandler.received.append(body)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


@pytest.fixture
def ingest_server():
    IngestHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), IngestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", IngestHandler.received
    server.shutdown()


def test_schema_is_valid_draft202012(schema):
    jsonschema.Draft202012Validator.check_schema(schema)


def test_all_shipped_events_validate(ingest_server, validator):
    """Exercise every RemoteStorage write path, then validate every batch
    payload that actually went over the wire against the schema."""
    endpoint, received = ingest_server
    storage = RemoteStorage(endpoint=endpoint, flush_interval=0.1, batch_size=1)
    try:
        rec = storage.save_prompt("rag-qa", "You are helpful", "abc123",
                                  metadata={"env": "prod"})
        run_id = storage.save_eval_run("suite1", prompt_name="rag-qa",
                                       overall_score=0.9)
        storage.save_eval_result(run_id, "t1", "semantic", True, score=0.85,
                                 details={"reason": "ok"}, latency_ms=12.3)
        storage.tag_prompt(rec.id, "prod")
        storage.save_dataset("ds1", [{"input": "a", "output": "b"}],
                             metadata={"src": "manual"})
        storage.save_vote("rag-qa", "the answer", 1, prompt_version=1,
                          message="great", metadata={"user": "u1"})
        time.sleep(0.8)
    finally:
        storage.close()

    assert received, "no events were shipped"

    seen_types = set()
    for batch in received:
        validator.validate(batch)  # raises on contract violation
        for ev in batch["events"]:
            seen_types.add(ev["type"])

    # Every distinct event type RemoteStorage emits must have been covered.
    assert seen_types == {
        "prompt_save", "eval_run", "eval_result",
        "prompt_tag", "dataset_save", "vote",
    }, seen_types


def _envelope(event: TelemetryEvent) -> dict:
    """Reproduce exactly the payload shape _ship_batch builds."""
    return {
        "events": [
            {"type": event.event_type, "data": event.data,
             "timestamp": event.timestamp},
        ]
    }


def test_invocation_event_validates(validator):
    """The invocation event type (emitted by promptry-js trackInvocation)
    is part of the shared contract."""
    ev = TelemetryEvent(event_type="invocation", data={
        "name": "rag-qa",
        "model": "claude-opus-4-8",
        "tokens_in": 120,
        "tokens_out": 55,
        "cost": 0.0123,
        "latency_ms": 842.1,
        "request_id": "req-1",
        "metadata": {"project_id": "my-app"},
        "created_at": "2026-07-07T00:00:00.000Z",
    })
    validator.validate(_envelope(ev))


def test_feedback_event_validates(validator):
    ev = TelemetryEvent(event_type="feedback", data={
        "request_id": "req-1",
        "rating": 1,
        "comment": "great answer",
        "source": "thumbs",
        "created_at": "2026-07-07T00:00:00.000Z",
    })
    validator.validate(_envelope(ev))


def test_unknown_event_type_rejected(validator):
    ev = TelemetryEvent(event_type="not_a_real_type", data={"x": 1})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(_envelope(ev))


def test_prompt_save_missing_field_rejected(validator):
    ev = TelemetryEvent(event_type="prompt_save", data={
        "name": "x", "content": "c", "hash": "h",
        # missing version, metadata, created_at
    })
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(_envelope(ev))

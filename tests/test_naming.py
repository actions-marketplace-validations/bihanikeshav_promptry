"""Zero-config prompt naming: precedence ladder, callsite inference, dedup vars."""
from promptry import naming


class TestPrecedence:
    def test_explicit_wins(self):
        assert naming.infer_task("checkout") == "checkout"

    def test_ambient_override(self):
        with naming.task("billing"):
            assert naming.infer_task() == "billing"
        # restored after the block
        assert naming.infer_task() != "billing"

    def test_explicit_beats_ambient(self):
        with naming.task("billing"):
            assert naming.infer_task("explicit") == "explicit"


class TestCallsiteInference:
    def test_infers_module_and_qualname(self):
        name = naming.infer_task()
        # called from this test method -> module:Class.method
        assert name == "tests.test_naming:TestCallsiteInference.test_infers_module_and_qualname" \
            or name.endswith(":TestCallsiteInference.test_infers_module_and_qualname")

    def test_walks_past_lambda_to_named_function(self):
        name = (lambda: naming.infer_task())()
        assert "<lambda>" not in name
        assert name.endswith(":TestCallsiteInference.test_walks_past_lambda_to_named_function")

    def test_walks_past_comprehension(self):
        names = [naming.infer_task() for _ in range(1)]
        assert "<listcomp>" not in names[0]
        assert names[0].endswith(":TestCallsiteInference.test_walks_past_comprehension")

    def test_stable_across_calls_and_memoized(self):
        a = naming.infer_task()
        b = naming.infer_task()
        # same call site (this function) -> identical, and independent of any
        # content; the memo cache holds the resolved name.
        assert a == b
        assert a.endswith(":TestCallsiteInference.test_stable_across_calls_and_memoized")


class TestSuppression:
    def test_suppress_and_resume(self):
        assert naming.is_suppressed() is False
        token = naming.suppress_capture()
        try:
            assert naming.is_suppressed() is True
        finally:
            naming.resume_capture(token)
        assert naming.is_suppressed() is False


def _module_level_helper():
    # a plain module-level function -> module:qualname
    return naming.infer_task()


class TestHelpers:
    def test_module_qualified(self):
        assert _module_level_helper() == "tests.test_naming:_module_level_helper"

    def test_top_package(self):
        assert naming._top_package("myapp.pipeline.rag") == "myapp"
        assert naming._top_package("") == ""

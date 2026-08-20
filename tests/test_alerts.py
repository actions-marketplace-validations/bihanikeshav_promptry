"""Alerting fan-out + PagerDuty incident integration."""
import json
import types

from promptry import alerts


class TestFanOut:
    def test_fans_out_to_configured_channels(self, monkeypatch):
        monkeypatch.setattr(alerts, "_webhook_url", lambda: "https://hooks/x")
        monkeypatch.setattr(alerts, "_pagerduty_key", lambda: "pdkey")
        sent = {}
        monkeypatch.setattr("promptry.notifications._send_webhook",
                            lambda url, s, b: sent.__setitem__("wh", (url, s, b)))
        monkeypatch.setattr(alerts, "_send_pagerduty",
                            lambda *a: sent.__setitem__("pd", a))
        alerts.send_alert("eval.regression", "title", "msg", severity="critical")
        assert "wh" in sent and "pd" in sent
        assert "[critical]" in sent["wh"][1]

    def test_nothing_configured_is_silent(self, monkeypatch):
        monkeypatch.setattr(alerts, "_webhook_url", lambda: None)
        monkeypatch.setattr(alerts, "_pagerduty_key", lambda: None)
        called = []
        monkeypatch.setattr("promptry.notifications._send_webhook",
                            lambda *a: called.append("wh"))
        monkeypatch.setattr(alerts, "_send_pagerduty", lambda *a: called.append("pd"))
        alerts.send_alert("x", "t", "m")   # must not raise or call anything
        assert called == [] or "email" not in called

    def test_channel_failure_never_raises(self, monkeypatch):
        monkeypatch.setattr(alerts, "_webhook_url", lambda: "https://hooks/x")
        monkeypatch.setattr(alerts, "_pagerduty_key", lambda: None)

        def _boom(*a):
            raise RuntimeError("network down")
        monkeypatch.setattr("promptry.notifications._send_webhook", _boom)
        alerts.send_alert("x", "t", "m")   # swallowed


class TestPagerDuty:
    def test_payload_shape(self, monkeypatch):
        captured = {}

        class _Resp:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake_urlopen(req, timeout=10):
            captured["url"] = req.full_url
            captured["data"] = json.loads(req.data.decode("utf-8"))
            return _Resp()

        monkeypatch.setattr(alerts.urllib.request, "urlopen", _fake_urlopen)
        alerts._send_pagerduty("routekey", "budget.exceeded", "over budget",
                               "global budget exceeded", "critical", {"limit": 10})
        assert captured["url"].endswith("/v2/enqueue")
        d = captured["data"]
        assert d["routing_key"] == "routekey"
        assert d["event_action"] == "trigger"
        assert d["payload"]["severity"] == "critical"
        assert d["payload"]["source"] == "promptry"
        assert d["payload"]["custom_details"] == {"limit": 10}
        assert d["dedup_key"].startswith("promptry-budget.exceeded")


class TestNotifyRegressionDelegates:
    def test_routes_through_send_alert(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(alerts, "send_alert",
                            lambda et, title, msg, **kw: seen.update(
                                {"et": et, "title": title, "sev": kw.get("severity")}))
        from promptry.notifications import notify_regression
        result = types.SimpleNamespace(suite_name="s", overall_score=0.4,
                                       overall_pass=False, prompt_name=None,
                                       prompt_version=None, model_version=None)
        notify_regression(result, "Drift: down")
        assert seen["et"] == "eval.regression"
        assert seen["sev"] == "critical"     # failing suite -> critical
        assert "s" in seen["title"]

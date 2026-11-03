"""The site's own provider accounts ran dry on 2026-10-31/11-01 (Gemini prepaid, Atlas Cloud, Anthropic) and nothing
told anyone: an out-of-credit error now mails the admins, once per provider per few hours."""
from webapp import provider_alerts as pa


def test_out_of_credit_errors_are_named_by_provider():
    assert pa.which("Gemini HTTP 402: Your prepayment credits are depleted.")[0].startswith("Gemini")
    assert pa.which("Atlas Cloud: insufficient balance")[0] == "Atlas Cloud"
    assert pa.which("Error code: 400 - Your credit balance is too low to access the Anthropic API")[0] == "Anthropic API"
    assert pa.which("Gemini HTTP 503: overloaded") is None and pa.which("") is None


def test_one_mail_per_provider_per_window(monkeypatch):
    monkeypatch.setattr(pa, "_last", {})
    sent = []
    tell = lambda text, subject: sent.append((subject, text))
    assert pa.check("Gemini HTTP 402: prepayment credits are depleted", tell=tell, now=1000)
    assert not pa.check("Gemini HTTP 402: prepayment credits are depleted", tell=tell, now=2000)
    assert pa.check("insufficient balance", tell=tell, now=2000), "another provider has its own window"
    assert pa.check("Gemini HTTP 402", tell=tell, now=1000 + pa.ALERT_SECONDS + 1)
    assert [s for s, _ in sent] == ["Gemini (Google AI Studio prepaid) is out of credit", "Atlas Cloud is out of credit",
                                    "Gemini (Google AI Studio prepaid) is out of credit"]
    assert "Top it up at" in sent[0][1]
    assert not pa.check("a plain bug", tell=tell, now=9e9)


def test_an_alert_that_cant_be_sent_never_breaks_the_request(monkeypatch):
    monkeypatch.setattr(pa, "_last", {})
    def broken(text, subject):
        raise RuntimeError("mail down")
    assert pa.check("insufficient balance", tell=broken, now=1) is False

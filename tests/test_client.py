import importlib.util
import pathlib


def _client():
    spec = importlib.util.spec_from_file_location("museboard", pathlib.Path(__file__).resolve().parents[1] / "client" / "museboard.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_client_allows_every_field_the_update_api_accepts():
    """The client keeps its own allow-list: it must never lag behind the backend schema (it once rejected action_results)."""
    from app.schema import UpdateRequest
    api_fields = set(UpdateRequest.model_fields) - {"idempotency_key"}        # the key is a separate keyword of update()
    assert set(_client().SECTIONS) == api_fields


def test_client_rejects_unknown_sections_before_any_network_call():
    c = _client()
    try:
        c.update(not_a_section=1)
    except c.MuseError as e:
        assert "unknown section" in str(e)
    else:
        raise AssertionError("expected MuseError")

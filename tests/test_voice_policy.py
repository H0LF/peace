"""Voice diagnostic consent and payload validation, without audio hardware or a database."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from peace import assistant
from peace.routes import VoicePrefs

NOW = datetime(2026, 10, 26, 21, tzinfo=UTC)


@pytest.fixture
def voice_store(monkeypatch):
    store = {'desktop': {'seen_at': NOW.isoformat()}}
    monkeypatch.setattr(assistant, '_pref', lambda session, key: dict(store.get(key, {})))
    monkeypatch.setattr(assistant, '_put', lambda session, key, value: store.update({key: dict(value)}))
    return store, SimpleNamespace(commit=lambda: None,
        scalar=lambda query: SimpleNamespace(value=store['voice_test']) if 'voice_test' in store else None)


def test_diagnostics_need_connected_desktop_and_expire(voice_store):
    store, session = voice_store
    body = assistant.VoiceTestIn(action='meter', settings=VoicePrefs())
    with pytest.raises(HTTPException) as exc:
        assistant.start_voice_test(body, session, NOW + timedelta(minutes=5))
    assert exc.value.status_code == 409
    job = assistant.start_voice_test(body, session, NOW)
    assert job['state'] == 'pending'
    assert assistant.pending_voice_test(session, NOW + timedelta(seconds=16)) is None
    assert assistant.voice_test_status(session, NOW + timedelta(seconds=16))['state'] == 'error'


def test_a_diagnostic_is_claimed_once_and_cannot_overlap(voice_store):
    store, session = voice_store
    body = assistant.VoiceTestIn(action='stt', settings=VoicePrefs(input_device='alsa_input.usb'))
    job = assistant.start_voice_test(body, session, NOW)
    with pytest.raises(HTTPException) as exc:
        assistant.start_voice_test(body, session, NOW)
    assert exc.value.status_code == 409
    assert assistant.pending_voice_test(session, NOW)['id'] == job['id']
    assert assistant.pending_voice_test(session, NOW) is None
    assistant.voice_test_result(job['id'], assistant.VoiceTestResult(state='done'), session, NOW)
    assert 'settings' not in store['voice_test']
    assert assistant.start_voice_test(body, session, NOW)['id'] != job['id']


def test_result_cannot_be_written_to_a_different_or_expired_job(voice_store):
    _, session = voice_store
    job = assistant.start_voice_test(assistant.VoiceTestIn(action='meter', settings=VoicePrefs()), session, NOW)
    assistant.pending_voice_test(session, NOW)
    result = assistant.VoiceTestResult(state='done')
    for id_, now in [('wrong', NOW), (job['id'], NOW + timedelta(minutes=4))]:
        with pytest.raises(HTTPException) as exc:
            assistant.voice_test_result(id_, result, session, now)
        assert exc.value.status_code == 409


def test_completed_diagnostic_cannot_be_replayed(voice_store):
    _, session = voice_store
    job = assistant.start_voice_test(assistant.VoiceTestIn(action='meter', settings=VoicePrefs()), session, NOW)
    assistant.pending_voice_test(session, NOW)
    assistant.voice_test_result(job['id'], assistant.VoiceTestResult(state='done'), session, NOW)
    with pytest.raises(HTTPException):
        assistant.voice_test_result(job['id'], assistant.VoiceTestResult(state='running'), session, NOW)


@pytest.mark.parametrize('name', ['--target', 'x\nsecret', '$(touch x)', 'a; b'])
def test_device_names_reject_arguments_and_control_characters(name):
    with pytest.raises(ValidationError):
        VoicePrefs(input_device=name)
    with pytest.raises(ValidationError):
        VoicePrefs(output_device=name)


def test_diagnostic_results_refuse_transcripts_or_recordings():
    with pytest.raises(ValidationError):
        assistant.VoiceTestResult(state='done', transcript='private speech')
    with pytest.raises(ValidationError):
        assistant.VoiceTestIn(action='listen_forever', settings=VoicePrefs())

import json
from pathlib import Path

import pytest
from eval.runner import percentile,redact


def test_versioned_suite_has_30_distinct_audio_scenarios_and_expected_coverage():
    suite=json.loads((Path(__file__).resolve().parents[1]/'eval/scenarios/suite.json').read_text())
    scenarios=suite['scenarios']
    assert len(scenarios)>=30 and len({s['id'] for s in scenarios})==len(scenarios)
    assert any(s.get('phone') for s in scenarios)
    assert any(s.get('snr_db') for s in scenarios)
    assert any(s.get('speed') for s in scenarios)
    assert any(s.get('fail_writes') for s in scenarios)
    assert any(isinstance(step,dict) and step.get('interrupt') for s in scenarios for step in s['steps'])


def test_report_percentiles_do_not_create_values_for_missing_observations():
    assert percentile([None]) == {'n':0,'p50':None,'p95':None}
    assert percentile([1,2,3])['p50']==2


def test_saved_transcript_redacts_contact_and_dob_data():
    value=redact('Call 212-555-0199, born 01/02/1990, email sam@example.com')
    assert '212' not in value and '1990' not in value and 'sam@' not in value

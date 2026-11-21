"""Publish tables from one actual completed full-suite report."""
import argparse
import json
from pathlib import Path


def render(path):
    report=json.loads(path.read_text())
    if report['status']!='completed' or len(report['scenarios'])<30 or not report['mode'].startswith('REAL'):
        raise ValueError('A complete real 30-scenario report is required')
    summary=report['summary']
    lines=['# Real voice evaluation results','',f"Source: [raw report](results/{path.name}).",
        f"Mode: {report['mode']}. Seed {report['seed']}; reference date {report['reference_date']}.",
        f"Commit `{report['metadata']['git_commit']}`; dirty tree: `{report['metadata']['git_dirty']}`.",
        '', '| Metric | Result |','|---|---:|',
        f"| Scenarios | {summary['scenario_count']} |",
        *[f"| {label} | {summary[key]*100:.2f}% |" for label,key in [
            ('Task success','task_success_rate'),('False confirmations','false_confirmation_rate'),
            ('Duplicate booking writes','duplicate_write_rate'),('ASR WER','asr_wer'),
            ('Phone ASR WER','phone_asr_wer'),('Barge-in handling','barge_in_success_rate')] if summary[key] is not None],
        f"| Voice-to-voice p50 / p95 (ms) | {summary['voice_to_voice_ms']['p50']:.1f} / {summary['voice_to_voice_ms']['p95']:.1f} |",
        f"| Barge-in stop dispatch p50 / p95 (ms) | {summary['barge_in_stop_ms']['p50']:.1f} / {summary['barge_in_stop_ms']['p95']:.1f} |",'']
    peak=report.get('memory',{}).get('sum_max_rss_bytes')
    if peak:lines += [f'Conservative backend/runner process peak-RSS sum: **{peak/1e9:.3f} GB**.','']
    lines += ['| Scenario | Task | WER | False claims | Duplicate rows |','|---|---|---:|---:|---:|']
    for s in report['scenarios']:
        lines.append(f"| {s['id']} | {'PASS' if s['task_success'] else 'FAIL'} | {s['asr_wer']:.3f} | {s['false_confirmations']} | {s['duplicate_writes']} |")
    lines += ['', '## Measurement limits','',
        report['transport']+'.',report['scoring']+'.',
        'Caller speaker 20 and agent speaker 0 share the real Piper acoustic model.',
        'Success for negative scenarios means correctly refusing or leaving state unchanged.',
        'This is a bounded fixture suite, not a guarantee about arbitrary callers.',
        'Phone cases measure codec degradation; ingress jitter/loss scheduling has separate tests.',
        'WER uses the Phase 0 normalization (digits 0–12 and Dr./Mr./Mrs.); joined time',
        'spellings such as 10am versus ten AM still count as differences.',
        'Voice latency ends at the first emitted audio chunk. Barge-in uses a 32 ms',
        'input-frame clock and synthetic playback-stop dispatch, not acoustic hardware output.',
        'Raw model files, settings, source/scenario hashes, actual state, traces and memory',
        'time output are in the JSON report. No user microphone audio is stored.','']
    return '\n'.join(lines)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('report',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.write_text(render(args.report));print(args.output)

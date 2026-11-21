import {useEffect, useState} from 'react';

async function read(path: string) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

const percent = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(1)}%`;

export function Review({active}: {active: boolean}) {
  const [reports, setReports] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [status, setStatus] = useState<any>({status:'idle'});
  const [error, setError] = useState('');
  async function refresh() {
    try {
      await read('/api/session');
      const [list, progress] = await Promise.all([read('/api/eval/reports'), read('/api/eval/status')]);
      setReports(list.reports); setStatus(progress);
    } catch (e) { setError(String(e)); }
  }
  useEffect(() => { void refresh(); const id = setInterval(refresh, 4000); return () => clearInterval(id); }, []);
  async function start() {
    setError('');
    const response = await fetch('/api/eval/start', {method:'POST'});
    if (!response.ok) setError((await response.json()).detail);
    await refresh();
  }
  return <section className="evaluation">
    <div className="section-title"><div><p className="eyebrow">EYWA EVAL</p><h2>Evidence, from actual calls.</h2></div>
      <button className="secondary" disabled={active || status.status === 'running'} onClick={start}>Run 30 voice scenarios</button></div>
    <p className="note">Real models, a different caller voice, fixed seed 42. The suite owns the single call slot while running. Replay audio is synthetic only.</p>
    {status.status === 'running' && <div role="status">Evaluating {status.completed}/{status.total}: {status.scenario}<button className="secondary" onClick={() => fetch('/api/eval/stop', {method:'POST'})}>Stop evaluation</button></div>}
    {error && <p role="alert">{error}</p>}
    <div className="report-list">{reports.length ? reports.map(report => <button className="report-card secondary" key={report.id} onClick={async () => setSelected(await read('/api/eval/reports/' + report.id))}>
      <strong>{report.id}</strong><span>{report.summary ? `${report.summary.scenario_count} scenarios · ${percent(report.summary.task_success_rate)} task success` : report.status}</span><small>{report.mode}</small>
    </button>) : <p className="placeholder">No evaluation reports yet. Run the suite after ending your live call.</p>}</div>
    {selected && <div className="report-detail"><h3>Run {selected.id}</h3><p className="note">{selected.transport}</p>
      <div className="score-grid">{[['Task success','task_success_rate'],['False confirmation','false_confirmation_rate'],['Duplicate writes','duplicate_write_rate'],['ASR WER','asr_wer'],['Phone ASR WER','phone_asr_wer'],['Barge-in success','barge_in_success_rate']].map(([label,key]) => <div key={key}><small>{label}</small><strong>{percent(selected.summary?.[key])}</strong></div>)}</div>
      <p>Voice to voice: p50 {selected.summary?.voice_to_voice_ms?.p50?.toFixed(0) ?? '—'} ms · p95 {selected.summary?.voice_to_voice_ms?.p95?.toFixed(0) ?? '—'} ms</p>
      <a href={'/api/eval/reports/' + selected.id} download>Download JSON report</a>
      <div className="scenario-list">{selected.scenarios.map((scenario:any) => <details key={scenario.id}><summary><span className={scenario.task_success ? 'pass' : 'fail'}>{scenario.task_success ? 'PASS' : 'FAIL'}</span> {scenario.id} · WER {percent(scenario.asr_wer)}</summary>
        {scenario.error_class && <p role="alert">{scenario.error_class}</p>}
        <audio controls preload="none" src={`/api/eval/audio/${selected.id}/${scenario.id}`}/>
        <p className="note">{scenario.replay_content ?? 'Generated agent output from this synthetic scenario.'}</p>
        {scenario.trace.map((turn:any,i:number) => <p key={i}><strong>{turn.role}: </strong>{turn.text}</p>)}
        <pre>{JSON.stringify({state:scenario.state, stage_metrics:scenario.stage_metrics, barge_in:scenario.barge_in}, null, 2)}</pre>
      </details>)}</div>
    </div>}
  </section>;
}

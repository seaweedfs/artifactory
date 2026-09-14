"""Publish separately attributable schema-1 rows after review of the sweep index."""
import datetime, hashlib, json, os, pathlib, re, shlex, subprocess, sys
R=pathlib.Path(os.environ['SWEEP_ROOT'])
B=pathlib.Path(os.environ.get('SWEEP_PAIRED_ROOT', str(R/'paired')))
P=os.environ['SWEEP_PRODUCT']
H=os.environ['SWEEP_HARNESS']
W=os.environ.get('SWEEP_WIKI','sweep3-unpublished')
if os.environ.get('SWEEP_DRY_RUN')=='1':print('DRY sweep2-status');raise SystemExit
OUT=R/'lab-status';OUT.mkdir(exist_ok=True)
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
index=json.loads((R/'sweep2-index.json').read_text())
rows=[]
for entry in index['rows']:
    if not entry.get('functional_counted',True): continue
    actual=entry['actual'].lower()
    key=re.sub('[^a-z0-9-]+','-',entry['gate_id'].lower()).strip('-')
    rows.append({'id':key,'expected':entry.get('expected','GREEN').lower(),
        'actual':{'infra_error':'error'}.get(actual,actual),'run':entry.get('run_id') or 'not-run',
        'duration':entry.get('duration_s',0),'detail':str(entry.get('detail','')),
        'source':entry['source']})
for item in json.loads((R/'coverage-retained/test-counts.json').read_text()):
    assert item['failed']==0 and item['report_exists']
    assert item['instrumented_binaries_retained']==item['test_binaries']
    rows.append({'id':'coverage-'+item['profile'].replace(',','-'),'expected':'green','actual':'green',
        'run':'coverage-'+item['profile'],'duration':0,'source':str(R/'coverage-retained'),
        'detail':f"Collection success: {item['passed']} unit PASS, {item['ignored']} ignored. Exact instrumented binaries/raw profiles retained. Source counts include inline tests; product-only branch coverage NOT MEASURED; ignored hardware paths not covered."})
audit=json.loads((B/'independent-audit.json').read_text())
for mode,m in audit['metrics'].items():
    classification=m['classification']
    actual='green' if classification=='NO_EVIDENCE_WITHIN_NOISE_BAND' else 'red' if classification=='STOP_REGRESSION_BELOW_MINUS_5' else 'error'
    rows.append({'id':'paired-'+mode,'expected':'green','actual':actual,'run':'paired-'+P[:8]+'-'+mode,
        'duration':0,'source':str(B/'independent-audit.json'),
        'detail':f"{classification}; delta={m['delta_pct']:.6f}%; conditional95%={m['conditional_95pct_interval_pct']}; order={m['order_delta_pct']}; lag1={m['lag1']:.6f}; reference {os.environ.get('SWEEP_REFERENCE_PRODUCT', os.environ.get('SWEEP_REFERENCE','unknown'))}. 'error' represents unresolved measurement, never a product RED. No historical exception inherited."})
for item in index['out_of_scope']:
    rows.append({'id':item['gate_id'],'expected':'green','actual':'skipped','run':'not-run','duration':0,
        'source':'owner ruling #665','detail':item['reason']})
written=[]
assert len({r['id'] for r in rows})==len(rows)
for row in rows:
    gate='sweep-'+P[:8]+'-'+row['id']
    assert re.fullmatch('[a-z0-9][a-z0-9-]*',gate)
    assert row['actual'] in ('green','red','error','skipped')
    assert row['expected'] in ('green','red')
    doc={'schema':1,'gate_id':gate,'title':'Second integration sweep: '+row['id'],'generated_at':now,
        'host':'M01/M02','commit':{'repo':'seaweed-mono','sha':P,'branch':'rdma/dev'},
        'expected':row['expected'],'actual':row['actual'],'agrees':row['expected']==row['actual'],
        'duration_s':row['duration'],'evidence':{'run_id':row['run']},
        'detail':f"Wiki {W}; product {P}; harness {H}; source {row['source']}. {row['detail']} Standing RED interpretation is on the wiki; raw expected/actual preserved. Initial invocation failures retained separately."}
    path=OUT/(gate+'.json');path.write_text(json.dumps(doc,indent=2)+'\n');written.append(path)
print(json.dumps({'emitted':len(written),'directory':str(OUT)}),flush=True)
if '--publish' in sys.argv:
    host,target=os.environ['TESTOPS_INBOX_DEST'].split(':',1)
    assert host=='testdev@192.168.1.181' and target=='/'
    verified=[]
    for path in written:
        subprocess.run([str(R/'testops'),'status','publish','--file',str(path)],check=True,stdout=subprocess.DEVNULL)
        got=subprocess.check_output(['ssh','-o','BatchMode=yes',host,'sha256sum '+shlex.quote('/opt/ci-inbox/lab/'+path.name)],text=True).split()[0]
        own=hashlib.sha256(path.read_bytes()).hexdigest();assert got==own
        verified.append({'file':path.name,'sha256':own,'readback':True})
    (R/'lab-status-readback.json').write_text(json.dumps(verified,indent=2))
    print(json.dumps({'published_readback':len(verified)}))

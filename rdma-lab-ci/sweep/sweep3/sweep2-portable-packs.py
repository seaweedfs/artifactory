#!/usr/bin/env python3
import datetime,fcntl,hashlib,json,os,pathlib,subprocess,yaml
if os.environ.get('SWEEP_DRY_RUN')=='1': print('DRY sweep2-portable-packs'); raise SystemExit
R=pathlib.Path(os.environ['SWEEP_ROOT']);P=pathlib.Path(os.environ['SWEEP_PRODUCT_TREE']);H=pathlib.Path(os.environ['SWEEP_HARNESS_TREE'])
SHA=os.environ['SWEEP_PRODUCT'];HSHA=os.environ['SWEEP_HARNESS'];B=R/'bin'
os.environ['PATH']='/opt/work/gate561-venv/bin:/home/testdev/.cargo/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
os.environ['TESTOPS_CARGO_TARGET_DIR']='/opt/work/cargo-target-volume';os.environ['TESTOPS_LMCACHE_PYTHON']='/opt/work/codex-lm-cpu-runtime/bin/python';os.environ['CI_PORT_BASE']='22000'
def sha(path): return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
def run(cmd,log,cwd=None,timeout=2700):
 with (R/log).open('w') as out:
  try: code=subprocess.run(cmd,cwd=cwd,stdout=out,stderr=subprocess.STDOUT,timeout=timeout).returncode
  except subprocess.TimeoutExpired: code=124
 print(json.dumps({'command':cmd,'exit':code,'log':str(R/log)}),flush=True);return code
def status(name,code):
 with (R/'portable-exits.txt').open('a') as f: f.write(f'{name} {code}\n')
lock=open('/mnt/smb/work/share/testops/locks/rdma-lab.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
activity=open('/mnt/smb/work/share/testops/WHO-IS-RUNNING','a',buffering=1)
activity.write('START codex02 sweep3 portable packs '+SHA+' '+datetime.datetime.now(datetime.timezone.utc).isoformat()+'\n')
saved=[(P/'enterprise/rust/Cargo.lock',R/'workspace.lock.before'),(P/'enterprise/seaweed-volume/Cargo.lock',R/'volume.lock.before')]
try:
 for src,dst in saved: dst.write_bytes(src.read_bytes())
 code=run(['go','build','-o',str(R/'testops'),'./testops/cmd/testops'],'portable-runner-build.log',H/'enterprise',600)
 if code!=0: status('runner-build',code); raise SystemExit(0)
 for pack in ['general','rotation','async','lm-e2e']:
  print('BEGIN '+pack+' '+datetime.datetime.now(datetime.timezone.utc).isoformat(),flush=True)
  cmd=[str(R/'testops'),'ci','run','--pack',pack,'--target','local','--mode','release-candidate','--source-dir',str(H),'--tooling-commit',HSHA,'--product-dir',str(P),'--commit',SHA,'--output-dir',str(R/pack),'--timeout','45m']
  status(pack,run(cmd,pack+'-console.log',None,3000))
  print('END '+pack+' '+datetime.datetime.now(datetime.timezone.utc).isoformat(),flush=True)
 volume=os.environ.get('SWEEP_VOLUME_BIN','/opt/work/bin/weed-volume-'+SHA+'-rdma')
 provenance={'product':SHA,'harness':HSHA,'binary_sha256':{str(p):sha(p) for p in [B/'weed',B/'sw-test-runner',volume]}}
 (R/'ts-repaired-provenance.json').write_text(json.dumps(provenance,indent=2))
 for name in ['kv-T1-process-lifecycle','kv-T2-lifecycle-rotation','kv-T8-crash-recovery']:
  spec_path=H/'enterprise/testops/scenarios'/(name+'.yaml')
  spec=yaml.safe_load(spec_path.read_text()); spec['topology']={'nodes':{'executor':{'is_local':True}}}
  data=R/(name+'-repaired'); data.mkdir(exist_ok=True)
  spec.setdefault('env',{}).update({'__testops_source_root':str(P),'__testops_binary':str(B/'weed'),'__testops_volume_binary':volume,'__testops_run_dir':str(data/'fixture'),'__testops_source_sha':SHA,'gate_script':str(H/'enterprise/testops/packs/kv/scripts/volume_cache_batches_gate.sh')})
  bindings=spec['env'].copy()
  for k,v in list(spec['env'].items()):
   if isinstance(v,str):
    for var,repl in bindings.items(): v=v.replace('{{ '+var+' }}',str(repl))
    spec['env'][k]=v
  scenario=data/'scenario.yaml'; scenario.write_text(yaml.safe_dump(spec,sort_keys=False))
  code=run([str(B/'sw-test-runner'),'run','-results-dir',str(data/'bundles'),'-tiers','core,devops,chaos','-allow-mutating',str(scenario)],name+'-repaired.log',H,2100)
  (data/'execution.json').write_text(json.dumps({'product':SHA,'harness':HSHA,'exit':code,'original_scenario_sha256':sha(spec_path),'changes':'executor local, pinned run paths and binaries only'}))
  status(name,code)
finally:
 for src,dst in saved:
  if dst.exists(): src.write_bytes(dst.read_bytes())
 activity.write('END codex02 sweep3 portable packs '+SHA+' '+datetime.datetime.now(datetime.timezone.utc).isoformat()+'\n')
 fcntl.flock(lock,fcntl.LOCK_UN)
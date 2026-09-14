#!/usr/bin/env python3
import os, pathlib, subprocess, json, datetime, fcntl, yaml
P=pathlib.Path(os.environ.get('SWEEP_PRODUCT_TREE','/opt/work/codex02-sweep2-product')); H=pathlib.Path(os.environ.get('SWEEP_HARNESS_TREE','/opt/work/codex02-sweep2-harness'))
R=pathlib.Path(os.environ['SWEEP_ROOT']); B=R/'bin'
os.environ['PATH']='/home/testdev/.cargo/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
lock=open('/mnt/smb/work/share/testops/locks/rdma-lab.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
a=open('/mnt/smb/work/share/testops/WHO-IS-RUNNING','a',buffering=1);a.write('START codex02 integration faults '+datetime.datetime.now(datetime.timezone.utc).isoformat()+'\n')
try:
 for name in ['testops-loopback-disk-full']:
  folder=R/(name+'-contained');folder.mkdir(exist_ok=True)
  spec=yaml.safe_load((H/'enterprise/testops/scenarios'/str(name+'.yaml')).read_text())
  spec['topology']={'nodes':{'executor':{'is_local':True}}}
  spec.setdefault('env',{}).update({'__testops_binary':str(B/'weed'),'__testops_run_dir':str(folder),'__testops_executor_host':'127.0.0.1','__testops_proto_dir':str(P/'enterprise/weed/pb'),'__testops_role_signal_binary':str(B/'testops-role-signal'),'__testops_chaos_fixture_binary':str(B/'testops-chaos-fixture'),'__testops_chaos_fixture_exe':'testops-chaos-fixture'})
  bindings=spec['env'].copy()
  for key,value in list(spec['env'].items()):
   if isinstance(value,str):
    for variable,replacement in bindings.items(): value=value.replace('{{ '+variable+' }}',str(replacement))
    spec['env'][key]=value
  file=folder/'scenario.yaml';file.write_text(yaml.safe_dump(spec,sort_keys=False))
  (folder/'fixture').mkdir(exist_ok=True)
  os.environ['TESTOPS_MIN_FREE_BYTES']='10737418240'
  os.environ['TESTOPS_RESOURCE_JOURNAL']=str(folder/'resource-journal.jsonl')
  with (folder/'console.log').open('w') as out:
   p=subprocess.run([str(B/'sw-test-runner'),'run','-results-dir',str(folder/'bundles'),'-tiers','core,devops,chaos','-allow-mutating',str(file)],cwd=H,stdout=out,stderr=subprocess.STDOUT,timeout=300)
  (folder/'execution.json').write_text(json.dumps({'exit':p.returncode,'product':os.environ['SWEEP_PRODUCT'],'harness':os.environ['SWEEP_HARNESS'],'scope':'product replication' if 'partition' in name else 'synthetic testops mechanism control'}))
  print(name+' exit='+str(p.returncode),flush=True)
finally:
 a.write('END codex02 integration faults '+datetime.datetime.now(datetime.timezone.utc).isoformat()+'\n');fcntl.flock(lock,fcntl.LOCK_UN)
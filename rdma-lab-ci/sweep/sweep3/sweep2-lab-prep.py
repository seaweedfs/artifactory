import datetime,fcntl,hashlib,json,os,pathlib,shutil,subprocess
R=pathlib.Path(os.environ['SWEEP_ROOT']);B=R/'bin'
P=pathlib.Path(os.environ['SWEEP_PRODUCT_TREE']);H=pathlib.Path(os.environ['SWEEP_HARNESS_TREE'])
SHA=os.environ['SWEEP_PRODUCT'];HSHA=os.environ['SWEEP_HARNESS']
if os.environ.get('SWEEP_DRY_RUN')=='1':print('DRY sweep2-lab-prep');raise SystemExit
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
with open('/mnt/smb/work/share/testops/locks/rdma-lab.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 B.mkdir(exist_ok=False)
 env=dict(os.environ,PATH='/home/testdev/.cargo/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin')
 for name in ['sw-test-runner','testops-role-signal','testops-chaos-fixture']:
  with (R/(name+'-build.log')).open('w') as log:
   subprocess.run(['go','build','-o',str(B/name),'./testops/cmd/'+name],cwd=H/'enterprise',env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
 with (R/'weed-build.log').open('w') as log:
  subprocess.run(['go','build','-o',str(B/'weed'),'./weed'],cwd=P/'enterprise',env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
 source=pathlib.Path(os.environ.get('SWEEP_VOLUME_BIN','/opt/work/bin/weed-volume-'+SHA+'-sweep-rdma'))
 dest=pathlib.Path('/opt/work/bin/weed-volume-'+SHA+'-rdma')
 assert not dest.exists();shutil.copyfile(source,dest);dest.chmod(0o755);assert sha(source)==sha(dest)
 cache=R/'t3-client';cache.mkdir()
 prior=pathlib.Path(os.environ['SWEEP_T3_CLIENT_SOURCE'])
 shutil.copyfile(prior/'t3-wire-client',cache/'t3-wire-client');(cache/'t3-wire-client').chmod(0o755)
 oldfixture=H/'enterprise/testops/packs/kv/testdata/cache_observation_wire_gate.rs'
 fixture=H/'enterprise/testops/packs/kv/testdata/cache_observation_wire_gate.rs'
 assert fixture.read_bytes()==oldfixture.read_bytes(),'T3 fixture changed; rebuild needed'
 (R/'lab-build-provenance.json').write_text(json.dumps({'product':SHA,'harness':HSHA,'binary_sha256':{str(p):sha(p) for p in [B/'weed',B/'sw-test-runner',dest,cache/'t3-wire-client']},'t3_client':'retained verified fixture-identical binary; no rebuild claim'},indent=2))
 print('lab binaries built and retained T3 client fixture equality verified',flush=True)

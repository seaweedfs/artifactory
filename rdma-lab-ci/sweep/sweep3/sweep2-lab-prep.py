import datetime,fcntl,hashlib,json,os,pathlib,shutil,subprocess
R=pathlib.Path('/data/nvme/testdev/codex02-integration-ac7f4e0f0-20260913');B=R/'bin'
P=pathlib.Path('/opt/work/codex02-sweep2-product');H=pathlib.Path('/opt/work/codex02-sweep2-harness')
SHA='ac7f4e0f08716ecc0ed1b418b5f26fdfb752c34f';HSHA='42dd1bb40de5ee8affebf799cbf86da64f54e2f5'
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
 source=pathlib.Path('/opt/work/bin/weed-volume-'+SHA+'-sweep-rdma')
 dest=pathlib.Path('/opt/work/bin/weed-volume-'+SHA+'-rdma')
 assert not dest.exists();shutil.copyfile(source,dest);dest.chmod(0o755);assert sha(source)==sha(dest)
 cache=R/'t3-client';cache.mkdir()
 prior=pathlib.Path('/data/nvme/testdev/codex02-integration-368fe309e-20260912/t3-client')
 shutil.copyfile(prior/'t3-wire-client',cache/'t3-wire-client');(cache/'t3-wire-client').chmod(0o755)
 oldfixture=pathlib.Path('/opt/work/codex02-integration-harness/enterprise/testops/packs/kv/testdata/cache_observation_wire_gate.rs')
 fixture=H/'enterprise/testops/packs/kv/testdata/cache_observation_wire_gate.rs'
 assert fixture.read_bytes()==oldfixture.read_bytes(),'T3 fixture changed; rebuild needed'
 (R/'lab-build-provenance.json').write_text(json.dumps({'product':SHA,'harness':HSHA,'binary_sha256':{str(p):sha(p) for p in [B/'weed',B/'sw-test-runner',dest,cache/'t3-wire-client']},'t3_client':'retained verified fixture-identical binary; no rebuild claim'},indent=2))
 print('lab binaries built and retained T3 client fixture equality verified',flush=True)

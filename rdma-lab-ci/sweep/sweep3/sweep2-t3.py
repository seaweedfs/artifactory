import hashlib, json, os, subprocess
from pathlib import Path

BASE=os.environ.get('T3_BASE',os.environ['SWEEP_PRODUCT'])
PIN=os.environ.get('T3_PRODUCT',BASE)
HARNESS=os.environ.get('T3_HARNESS',BASE)
tag=os.environ.get('T3_RESULT_TAG','codex02-sweep3-t3-'+os.environ['T3_TAG'])
results=Path('/data/nvme/testdev')/tag
share=Path('/mnt/smb/work/share/testops/results')/tag
source=Path('/opt/work/codex-step07a-mr/enterprise')
if HARNESS != BASE: source=Path(os.environ.get('SWEEP_HARNESS_TREE','/opt/work/codex02-sweep2-harness'))/'enterprise'
scripts=source/'testops/packs/kv/scripts'
client='testdev@192.168.1.181'
client_root='/opt/work/'+tag
cache=Path('/opt/work/codex-step05/tcp-client')
if HARNESS != BASE: cache=Path(os.environ['SWEEP_ROOT'])/'t3-client'
def call(args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
results.mkdir();share.mkdir()
if HARNESS == BASE:
    provenance=json.loads((cache/'provenance.json').read_text())
else:
    provenance=dict(harness_sha=HARNESS,dependency_sha=os.environ.get('T3_DEPENDENCY_SHA',''),
        fixture_sha256=sha(source/'testops/packs/kv/testdata/cache_observation_wire_gate.rs'),
        binary_sha256=sha(cache/'t3-wire-client'))
    (cache/'provenance.json').write_text(json.dumps(provenance,indent=2))
assert sha(source/'testops/packs/kv/testdata/cache_observation_wire_gate.rs')==provenance['fixture_sha256']
assert sha(cache/'t3-wire-client')==provenance['binary_sha256']
server_env=results/'server.env'
server_env.write_text('export PATH=/usr/local/go/bin:/home/testdev/.cargo/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin\n'
    +f'TESTOPS_T3_RUN_ROOT={results}\nTESTOPS_ACTIVITY_LOG=/mnt/smb/work/share/testops/WHO-IS-RUNNING\n'
    +'TESTOPS_LOCK_FILE=/mnt/smb/work/share/testops/locks/rdma-lab.lock\n'
    +'TESTOPS_T3_SERVER_IP=10.0.0.3\nTESTOPS_T3_DEVICE=rocep1s0\nTESTOPS_T3_GID_INDEX=3\nCI_PORT_BASE=27000\n')
client_env=results/'client.env'
client_env.write_text('export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin\n'
    +'TESTOPS_ACTIVITY_LOG=/mnt/smb/work/share/testops/WHO-IS-RUNNING\nTESTOPS_T3_DEVICE=rocep1s0\nTESTOPS_T3_GID_INDEX=3\n')
call(['ssh','-o','BatchMode=yes',client,'mkdir '+client_root])
call(['scp',str(cache/'t3-wire-client'),str(cache/'provenance.json'),str(scripts/'run_volume_cache_rdma_client_gate.sh'),str(client_env),client+':'+client_root+'/'])
assert subprocess.check_output(['ssh',client,'sha256sum '+client_root+'/t3-wire-client'],text=True).split()[0]==provenance['binary_sha256']
cpu={}
for host in ('192.168.1.184','192.168.1.181'):
    cpu[host]=subprocess.check_output(['ssh','testdev@'+host,'cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor /sys/devices/system/cpu/intel_pstate/no_turbo'],text=True).splitlines()
runner=str(Path(os.environ['SWEEP_ROOT'])/'bin/sw-test-runner')
weed=str(Path(os.environ['SWEEP_ROOT'])/'bin/weed')
volume='/opt/work/bin/weed-volume-'+PIN+'-rdma'
scenario=dict(name='rdma-integration-t3',timeout='8m',topology=dict(nodes=dict(server=dict(is_local=True),
    client=dict(host='192.168.1.181',user='testdev',key='/home/testdev/.ssh/id_ed25519'))),
    phases=[dict(name='stale_descriptor_fence',actions=[dict(action='kv_t3_rdma_gate_run',server_node='server',client_node='client',
        server_script=str(scripts/'run_volume_cache_rdma_gate.sh'),client_script=client_root+'/run_volume_cache_rdma_client_gate.sh',
        server_env_file=str(server_env),client_env_file=client_root+'/client.env',client_binary=client_root+'/t3-wire-client',
        weed_bin=weed,volume_bin=volume,expect_mode=os.environ.get('T3_EXPECT','baseline'),run_id='{{ run_id }}',shared_dir=str(share)+'/t3-{{ run_id }}',save_as='t3'),
        dict(action='kv_t3_rdma_gate_assert',result='{{ t3 }}')])])
scenario_path=results/'scenario.yaml';scenario_path.write_text(json.dumps(scenario,indent=2))
command=[runner,'run','-results-dir',str(results/'bundles'),'-tiers','core,devops,chaos','-allow-mutating',str(scenario_path)]
with (results/'runner.log').open('w') as log:
    proc=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=510)
manifests=list((results/'bundles').rglob('manifest.json'))
manifest=json.loads(manifests[0].read_text()) if len(manifests)==1 else {}
run=manifest.get('run_id')
payloads={};errors={}
for name in ('observation.json','drive.json','lifecycle.json'):
    try:payloads[name]=json.loads((share/('t3-'+run)/name).read_text())
    except (TypeError,OSError,ValueError) as error:errors[name]=str(error)
report=dict(product_sha=PIN,harness_sha=HARNESS,run_id=run,runner_exit=proc.returncode,manifest=manifest,
    results=payloads,parse_errors=errors,client=provenance,cpu=cpu,command=command,
    binary_sha256={Path(p).name:sha(p) for p in (weed,volume,runner)},
    verdict_note='Pinned TestOps baseline-mode fixture with unchanged assertion; inspect raw result, never relabel.')
(results/'complete.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2),flush=True)
print('RESULT_DIR='+str(results),flush=True)

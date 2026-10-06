"""Shared inert fixture controls. These never certify siw or GitHub runner survival."""
import copy
import fcntl
import importlib.util
import hashlib
import io
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
import base64
from unittest.mock import Mock, patch

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tm_adapter', SCRIPTS/'rdma-tm-diagnostic.py')
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)
d = a.decoder

def frame(name, body):
    body = body.encode()
    return ('TM_RAW name='+json.dumps(name)+' bytes='+str(len(body))+'\n').encode()+body+b'\n'

def evidence(objects, identity):
    """Fabricated process evidence for parser controls only, explicitly NOT provider proof."""
    raw=[]; stdout=['TM_SETUP connection_ns=1 grade=PROVIDER_IDENTITY_BOUND_SEPARATELY']
    token='client_object=0x123 pipe_ordinal=0 native_pd_relation=BLOCKED'
    maps='\n'.join('0-1 r-xp 0000 '+v['dev']+' '+str(v['inode'])+' '+v['path'] for v in objects.values())+'\n'
    stat=str(identity['pid'])+' (inert) S '+' '.join(['0']*18+[str(identity['starttime'])])+'\n'
    def snapshot(phase, correlation, key=None):
        raw.append(frame('correlation','phase='+phase+' '+correlation+' native_pd=UNAVAILABLE kernel_mr_inventory=UNAVAILABLE'))
        for name in ('/proc/self/stat','/proc/self/status','/proc/self/limits','/proc/self/numa_maps','/proc/sys/kernel/osrelease','/proc/modules','/proc/self/maps'):
            raw.append(frame(name, stat if name=='/proc/self/stat' else maps if name=='/proc/self/maps' else 'INERT_ONLY\n'))
        raw.extend([frame('process_fd','fd="3" target=Ok("/dev/infiniband/uverbs0")'),frame('uverbs_ibdev','fd="3" sysfs="/sys/class/infiniband_verbs/uverbs0/ibdev" ibdev=Ok("../../infiniband/siw0")'),frame('typed_census','process_fd_count=4 native_pd_relation=BLOCKED')])
        if phase=='live':
            for ordinal in range(key[2]): raw.append(frame('public_live_mr',correlation+f' ordinal={ordinal} len={key[1]//key[2]} lkey=1 rkey=1 scope=PUBLIC_HANDLE_NOT_PD'))
    snapshot('pool_baseline',token)
    for block in range(3):
        for j in range(3):
            size=d.SIZES[(j+block)%3]
            for slots in (1,8):
                for operation in range(31):
                    key=(block,size,slots,operation); correlation=token+f' block={block} bytes={size} slots={slots} operation={operation}'
                    for phase in ('before','live','after_checked_close'): snapshot(phase,correlation,key)
                    for ordinal in range(slots):
                        stdout.append(f'TM1-R/TM1-C block={block} operation={operation} warmup={str(operation==0).lower()} bytes={size} slots={slots} ordinal={ordinal} created_mrs={slots} registration_ns=10 cleanup_ns=Some(20) cleanup_error=None mr_len={size//slots} remaining_mrs=0 retained_bytes=0 same_client_object=true native_pd_relation=BLOCKED')
    return b''.join(raw),'\n'.join(stdout)+'\n'

HELPER = '''import pathlib,sys,subprocess,os,signal,json,time
root=pathlib.Path(sys.argv[2]); role=sys.argv[3]; phase=sys.argv[1]; directory=root/role
def identity(pid): return int(pathlib.Path('/proc/'+str(pid)+'/stat').read_text().split(') ',1)[1].split()[19])
if phase=='up':
 directory.mkdir(); child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)',str(root)],start_new_session=True)
 (directory/'owner.pid').write_text(str(child.pid)); (directory/'owner.starttime').write_text(str(identity(child.pid)))
 with (root/'fixture-owned.jsonl').open('a') as f:f.write(json.dumps(dict(pid=child.pid,starttime=identity(child.pid)))+'\\n')
elif phase=='down':
 if directory.exists():
  pid=int((directory/'owner.pid').read_text()); expected=int((directory/'owner.starttime').read_text())
  try: actual=identity(pid)
  except FileNotFoundError: actual=None
  assert actual is None or actual==expected
  if actual is not None: os.killpg(pid,signal.SIGTERM)
  time.sleep(.05)
elif phase=='probe': pass
else: raise ValueError(phase)
'''

class GoBootstrap(unittest.TestCase):
    def test_bytes_authority_source_and_clone_refusals(self):
        for case in ('positive','foreign-actor','wrong-source','payload-drift','bootstrap-drift','clone-drift','manifest-drift','clone-head-drift'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as directory:
                out=pathlib.Path(directory);env=dict(TM_PHASE='build',TM_SOURCE_SHA=a.GO_BOOTSTRAP_MONO,TM_BUILD_AUTHORITY='BUILD_ONLY '+a.GO_BOOTSTRAP_MONO,TM_MANAGER_ACTOR_ID='6647175',GITHUB_ACTOR_ID='6647175',GITHUB_WORKSPACE=str(out))
                blob=base64.b64decode(a.GO_BOOTSTRAP_B64,validate=True)
                self.assertEqual(hashlib.sha256(blob).hexdigest(),a.GO_BOOTSTRAP_SHA);self.assertIn(b'\ngo 1.26.6\n',blob)
                if case in ('foreign-actor','wrong-source','payload-drift'):
                    if case=='foreign-actor':env['GITHUB_ACTOR_ID']='1'
                    if case=='wrong-source':env['TM_SOURCE_SHA']='0'*40
                    with patch.object(a,'GO_BOOTSTRAP_B64',base64.b64encode(blob+b'DRIFT').decode() if case=='payload-drift' else a.GO_BOOTSTRAP_B64):
                        with self.assertRaisesRegex(ValueError,'BUILD_AUTHORITY_UNBOUND|GO_BOOTSTRAP_SOURCE|GO_BOOTSTRAP_BYTES'):a.go_bootstrap(out,env)
                    self.assertFalse((out/'INPUT/bootstrap').exists());self.assertEqual(json.loads((out/'go-bootstrap-before-setup.json').read_text())['state'],'FAIL');continue
                a.go_bootstrap(out,env);target=out/'INPUT/bootstrap/enterprise/go.mod';self.assertEqual(target.read_bytes(),blob)
                clone=out/'seaweedfs-source/enterprise/go.mod';clone.parent.mkdir(parents=True);clone.write_bytes(blob)
                if case=='bootstrap-drift':target.write_bytes(blob+b'DRIFT')
                if case=='clone-drift':clone.write_bytes(blob+b'DRIFT')
                if case=='manifest-drift':(out/'INPUT/bootstrap/manifest.sha256').write_text('DRIFT')
                with patch.object(a,'command',return_value=((('0'*40) if case=='clone-head-drift' else a.GO_BOOTSTRAP_MONO)+'\n').encode()) as head:
                    if case=='positive':a.go_bootstrap(out,env,True)
                    else:
                        with self.assertRaisesRegex(ValueError,'GO_BOOTSTRAP_DRIFT|GO_CLONE_MOD_DRIFT|INPUT_MANIFEST_DRIFT|SOURCE_HEAD_DRIFT'):a.go_bootstrap(out,env,True)
                record=json.loads((out/'go-bootstrap-clone-verify.json').read_text());self.assertEqual(record['state'],'PASS' if case=='positive' else 'FAIL')
                if case=='positive':head.assert_called_once();self.assertEqual(record['version'],'1.26.6')
    def test_workflow_order_exact_pin_and_default_jobs_unchanged(self):
        workflow=(SCRIPTS.parent/'workflows/rdma-softroce-tests.yml').read_text();diagnostic=workflow.split('  tm-connected-diagnostic:',1)[1]
        positions=[diagnostic.index(s) for s in ('Authenticate frozen mono go.mod','Setup diagnostic Go','First BUILD runner preflight','Clone exact mono','Compare cloned mono go.mod','Build all three')]
        self.assertEqual(positions,sorted(positions));self.assertIn('actions/setup-go@924ae3a1cded613372ab5595356fb5720e22ba16',diagnostic)
        setup=diagnostic[positions[1]:positions[2]];self.assertIn("if: inputs.diagnostic_phase == 'build'",setup);self.assertIn('timeout-minutes: 5',setup);self.assertIn('go-version-file: tm-diagnostic/INPUT/bootstrap/enterprise/go.mod',setup);self.assertIn('check-latest: false',setup);self.assertIn('cache: false',setup)
        # Exact LF prefix from reviewed 106e1fc1; no dependence on git/worktree aliases.
        self.assertEqual(hashlib.sha256(workflow.replace('options: [build, run, probe]','options: [build, run]').replace('options: [none, tm-connected-v1, r2-recovery-v1]','options: [none, tm-connected-v1]').split('  tm-connected-diagnostic:',1)[0].encode()).hexdigest(),'3d52314c5858d523a07793d9893aaa2f3160c9c11d0f1fa215f4d8ddb34e6a51')

class GoSetupReceipt(unittest.TestCase):
    def test_real_outcome_entry_preserves_refusals_and_bindings(self):
        for outcome in ('success','failure','cancelled','skipped','','foreign-action','missing-bootstrap','drift'):
            with self.subTest(outcome=outcome),tempfile.TemporaryDirectory() as directory:
                out=pathlib.Path(directory);env=dict(os.environ,TM_PHASE='build',TM_SOURCE_SHA=a.GO_BOOTSTRAP_MONO,TM_BUILD_AUTHORITY='BUILD_ONLY '+a.GO_BOOTSTRAP_MONO,TM_MANAGER_ACTOR_ID='6647175',GITHUB_ACTOR_ID='6647175',GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1',GITHUB_SHA='a'*40,TM_GO_SETUP_ACTION='924ae3a1cded613372ab5595356fb5720e22ba16',TM_GO_SETUP_OUTCOME=outcome)
                if outcome!='missing-bootstrap':a.go_bootstrap(out,env)
                if outcome=='foreign-action':env.update(TM_GO_SETUP_OUTCOME='success',TM_GO_SETUP_ACTION='b'*40)
                if outcome=='missing-bootstrap':env['TM_GO_SETUP_OUTCOME']='success'
                if outcome=='drift':env['TM_GO_SETUP_OUTCOME']='success';(out/'INPUT/bootstrap/enterprise/go.mod').write_bytes(b'WRONG')
                child=subprocess.run([sys.executable,str(SCRIPTS/'rdma-tm-diagnostic.py'),'setup-outcome',str(out)],env=env,capture_output=True,timeout=10)
                record=json.loads((out/'go-setup-outcome.json').read_text());self.assertEqual(record['outcome'],env['TM_GO_SETUP_OUTCOME']);self.assertEqual(record['run_id'],'123');self.assertEqual(record['ci_sha'],'a'*40);self.assertEqual(record['step_id'],'tm-go-setup');self.assertTrue(record['logs'])
                if outcome=='success':
                    self.assertEqual(child.returncode,0);self.assertEqual(record['state'],'PASS');a.admitted_go_setup(out,env)
                    for key in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_SHA','TM_SOURCE_SHA'):
                        bad=dict(env,**{key:'999' if key.startswith('GITHUB_RUN') else 'b'*40})
                        with self.assertRaisesRegex(ValueError,'GO_SETUP_NOT_ADMITTED'):a.admitted_go_setup(out,bad)
                else:
                    self.assertNotEqual(child.returncode,0);self.assertNotEqual(record['state'],'PASS')
                    with self.assertRaisesRegex(ValueError,'GO_SETUP_NOT_ADMITTED'):a.admitted_go_setup(out,env)
                    if outcome=='failure':self.assertEqual(record['error_code'],'GO_SETUP_FAILURE_OR_TIMEOUT')
                    if outcome=='cancelled':self.assertEqual(record['state'],'UNKNOWN');self.assertEqual(record['error_code'],'GO_SETUP_CANCELLED_UNKNOWN')
                    self.assertEqual(json.loads((out/'state.json').read_text())['state'],'REFUSED_OR_UNKNOWN')
                a.manifest(out)
    def test_always_outcome_consumer_precedes_admission_and_upload(self):
        diagnostic=(SCRIPTS.parent/'workflows/rdma-softroce-tests.yml').read_text().split('  tm-connected-diagnostic:',1)[1]
        start=diagnostic.index('Record diagnostic Go setup outcome');end=diagnostic.index('First BUILD runner preflight');consumer=diagnostic[start:end]
        self.assertLess(start,diagnostic.index('Upload raw'));self.assertIn("if: always() && inputs.diagnostic_phase == 'build'",consumer);self.assertIn('timeout-minutes: 1',consumer);self.assertIn('steps.tm-go-setup.outcome',consumer);self.assertIn('setup-outcome',consumer);self.assertIn('id: tm-go-setup',diagnostic);self.assertNotIn('continue-on-error',diagnostic)

class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='tm-ci-control-'); self.dir=pathlib.Path(self.temp.name)
        self.objects={}
        for name in ('libsiw','libibverbs','librdmacm'):
            f=self.dir/(name+'.so'); f.write_bytes(b'INERT OBJECT NOT PROVIDER'); st=f.stat()
            self.objects[name]=dict(path=str(f),sha256=a.sha(f),inode=st.st_ino,dev=f'{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}')
        self.identity=dict(pid=123,starttime=456)
    def tearDown(self):
        # Stop ONLY test-owned identities, after each refusal/retention assertion.
        root=self.dir/'lease'
        for f in (root/'fixture-owned.jsonl',root/'guard.identity.json',root/'client.identity.json'):
            if not f.is_file(): continue
            rows=[json.loads(line) for line in f.read_text().splitlines()] if f.suffix=='.jsonl' else [json.loads(f.read_text())]
            for row in rows:
                actual=a.proc(row['pid'])
                if actual and actual['starttime']==row['starttime']:
                    try: os.killpg(row['pid'],signal.SIGTERM)
                    except ProcessLookupError: pass
        time.sleep(.03); self.temp.cleanup()
    def reject(self, callable, reason):
        with self.assertRaisesRegex(ValueError,reason): callable()
    def test_routing_default_and_opt_in(self):
        for event in ('workflow_dispatch','repository_dispatch'):
            for profile in (None,'none'):
                self.assertEqual(a.routing(event,dict(diagnostic_profile=profile),'github-hosted'),'DEFAULT')
        self.assertEqual(a.routing('repository_dispatch',dict(diagnostic_profile='tm-connected-v1'),'self-hosted'),'DEFAULT')
        request=dict(diagnostic_profile='tm-connected-v1',runner='tp01',mono_sha='a'*40,diagnostic_phase='run')
        self.assertEqual(a.routing('workflow_dispatch',request,'self-hosted'),'DIAGNOSTIC')
        for key,value in [('diagnostic_profile','foreign'),('runner','ubuntu'),('mono_sha','main'),('diagnostic_phase','unknown')]:
            broken=dict(request,**{key:value}); self.reject(lambda:a.routing('workflow_dispatch',broken,'self-hosted'),'PROFILE_OR_RUNNER|PHASE_OR_SHA')
        self.reject(lambda:a.routing('workflow_dispatch',request,'github-hosted'),'PROFILE_OR_RUNNER')
    def test_r2_typed_parser_positive_and_all_refusals(self):
        """Inert records only; fields-valid is never provider qualification."""
        request=dict(diagnostic_profile='r2-recovery-v1',runner='tp01',mono_sha='a'*40,diagnostic_phase='run')
        self.assertEqual(a.routing('workflow_dispatch',request,'self-hosted'),'DIAGNOSTIC');self.assertEqual(a.routing('repository_dispatch',request,'self-hosted'),'DEFAULT')
        maps='\n'.join('0-1 r-xp 0000 '+v['dev']+' '+str(v['inode'])+' '+v['path'] for v in self.objects.values())+'\n'
        group={'/proc/self/maps':[maps],'process_fd':['fd="3" target=Ok("/dev/infiniband/uverbs0")'],'uverbs_ibdev':['fd="3" sysfs="/sys/class/infiniband_verbs/uverbs0/ibdev" ibdev=Ok("siw0")']}
        actors=[dict(self.identity,namespace=1),dict(pid=999,starttime=7,namespace=1)];providers={str(v['pid']):dict(decoder_alias_group=group) for v in actors}
        rows=[dict(label='initial',actors=actors,providers=providers,counts=[0]*7)];stdout=[]
        for cycle in range(18):
            connected=[1,1,1,cycle+1,cycle,0,cycle+1];settled=[0,0,0,cycle+1,cycle+1,0,0]
            rows.extend([dict(label=f'{cycle}-connected',actors=actors,providers=providers,counts=connected),dict(label=f'{cycle}-settled',actors=actors,providers=providers,counts=settled)])
            if 1<=cycle<=16:stdout.append(f'R2_TERMINAL cycle={cycle} wr_id={cycle+100} qp_num={cycle+1} status=10 vendor_err=0 retained=true id_matches=true')
            stdout.append(f'R2_CYCLE cycle={cycle} failed={str(1<=cycle<=16).lower()} capacity=32 counts={settled}')
        stdout+='R2_RECOVERY_PASS cycles=16 controls=2 same_process=123 remote_landing=NOT_CLAIMED','test result: ok. 1 passed; 0 failed'
        text='\n'.join(stdout);raw=lambda data:('\n'.join(map(json.dumps,data))+'\n').encode()
        self.assertEqual(d.recovery(raw(rows),text,self.objects,self.identity)['state'],'R2_RECOVERY_FIELDS_VALID_ONLY')
        # Captured register_metrics/gather_metrics CPU registry; not a running volume.
        fixture=dict(source_sha='a716580918937dec8ebf0c0fe2d449bfc9152002',sha256='fbc40680ca06d4f8a012cacaf5bde3bc6fd569c4f93b3a4f3133ba3925b5ad6f',text_b64='IyBIRUxQIFNlYXdlZWRGU19idWlsZF9pbmZvIEEgbWV0cmljIHdpdGggYSBjb25zdGFudCAnMScgdmFsdWUgbGFiZWxlZCBieSB2ZXJzaW9uLCBjb21taXQsIHNpemVsaW1pdCwgZ29vcywgYW5kIGdvYXJjaCBmcm9tIHdoaWNoIFNlYXdlZWRGUyB3YXMgYnVpbHQuCiMgVFlQRSBTZWF3ZWVkRlNfYnVpbGRfaW5mbyBnYXVnZQpTZWF3ZWVkRlNfYnVpbGRfaW5mb3tjb21taXQ9IiIsZ29hcmNoPSJ4ODZfNjQiLGdvb3M9ImxpbnV4IixzaXplbGltaXQ9IjgwMDBHQiIsdmVyc2lvbj0iODAwMEdCIDQuNDgifSAxCiMgSEVMUCBTZWF3ZWVkRlNfcmRtYV9jb25uZWN0aW9uc19hY2NlcHRlZF90b3RhbCBSRE1BIGNvbm5lY3Rpb25zIGFkbWl0dGVkIGJ5IHRoZSBwcm9jZXNzLXdpZGUgY29ubmVjdGlvbiBidWRnZXQuCiMgVFlQRSBTZWF3ZWVkRlNfcmRtYV9jb25uZWN0aW9uc19hY2NlcHRlZF90b3RhbCBjb3VudGVyClNlYXdlZWRGU19yZG1hX2Nvbm5lY3Rpb25zX2FjY2VwdGVkX3RvdGFsIDMKIyBIRUxQIFNlYXdlZWRGU19yZG1hX2Nvbm5lY3Rpb25zX2FjdGl2ZSBDdXJyZW50IFJETUEgY29ubmVjdGlvbnMgYWRtaXR0ZWQgYWNyb3NzIG9uZS1zaWRlZCBSQywgUkMgcHVzaC1yZWFkLCBhbmQgREMgcHVzaC1yZWFkIGxpc3RlbmVycy4KIyBUWVBFIFNlYXdlZWRGU19yZG1hX2Nvbm5lY3Rpb25zX2FjdGl2ZSBnYXVnZQpTZWF3ZWVkRlNfcmRtYV9jb25uZWN0aW9uc19hY3RpdmUgMgojIEhFTFAgU2Vhd2VlZEZTX3JkbWFfY29ubmVjdGlvbnNfZXN0YWJsaXNoZWRfdG90YWwgQWRtaXR0ZWQgUkRNQSBjb25uZWN0aW9ucyB3aG9zZSByZXF1aXJlZCBjb250cm9sL1FQIHBhaXJpbmcgY29tcGxldGVkLgojIFRZUEUgU2Vhd2VlZEZTX3JkbWFfY29ubmVjdGlvbnNfZXN0YWJsaXNoZWRfdG90YWwgY291bnRlcgpTZWF3ZWVkRlNfcmRtYV9jb25uZWN0aW9uc19lc3RhYmxpc2hlZF90b3RhbCAwCiMgSEVMUCBTZWF3ZWVkRlNfcmRtYV9jb25uZWN0aW9uc19yZWplY3RlZF90b3RhbCBSRE1BIGNvbm5lY3Rpb25zIHJlamVjdGVkIGJlY2F1c2UgdGhlIHByb2Nlc3Mtd2lkZSBjb25uZWN0aW9uIGJ1ZGdldCB3YXMgZnVsbC4KIyBUWVBFIFNlYXdlZWRGU19yZG1hX2Nvbm5lY3Rpb25zX3JlamVjdGVkX3RvdGFsIGNvdW50ZXIKU2Vhd2VlZEZTX3JkbWFfY29ubmVjdGlvbnNfcmVqZWN0ZWRfdG90YWwgMQojIEhFTFAgU2Vhd2VlZEZTX3JkbWFfY29ubmVjdGlvbnNfcmVsZWFzZWRfdG90YWwgUkRNQSBjb25uZWN0aW9uIHBlcm1pdHMgcmVsZWFzZWQgYWZ0ZXIgY29ubmVjdGlvbiB0ZWFyZG93bi4KIyBUWVBFIFNlYXdlZWRGU19yZG1hX2Nvbm5lY3Rpb25zX3JlbGVhc2VkX3RvdGFsIGNvdW50ZXIKU2Vhd2VlZEZTX3JkbWFfY29ubmVjdGlvbnNfcmVsZWFzZWRfdG90YWwgMgojIEhFTFAgU2Vhd2VlZEZTX3JkbWFfY29udHJvbF9oYWx2ZXNfcmVqZWN0ZWRfdG90YWwgT3V0LW9mLW9yZGVyIFJDIGNvbnRyb2wgaGFsdmVzIGRyb3BwZWQgYmVjYXVzZSB0aGUgYm91bmRlZCBwYWlyaW5nIG1hcCB3YXMgZnVsbC4KIyBUWVBFIFNlYXdlZWRGU19yZG1hX2NvbnRyb2xfaGFsdmVzX3JlamVjdGVkX3RvdGFsIGNvdW50ZXIKU2Vhd2VlZEZTX3JkbWFfY29udHJvbF9oYWx2ZXNfcmVqZWN0ZWRfdG90YWwgMAojIEhFTFAgU2Vhd2VlZEZTX3JkbWFfY29weV9ieXRlc190b3RhbCBUb3RhbCBieXRlcyBhZHZlcnRpc2VkIGZvciBvbmUtc2lkZWQgUkRNQSB2b2x1bWUtbW92ZSBjb3BpZXMuCiMgVFlQRSBTZWF3ZWVkRlNfcmRtYV9jb3B5X2J5dGVzX3RvdGFsIGNvdW50ZXIKU2Vhd2VlZEZTX3JkbWFfY29weV9ieXRlc190b3RhbCAwCiMgSEVMUCBTZWF3ZWVkRlNfcmRtYV9jb3B5X3N0YWdpbmdfYWN0aXZlIEN1cnJlbnQgbnVtYmVyIG9mIGluLWZsaWdodCBSRE1BIGNvcHkgcmVnaXN0cmF0aW9ucy4KIyBUWVBFIFNlYXdlZWRGU19yZG1hX2NvcHlfc3RhZ2luZ19hY3RpdmUgZ2F1Z2UKU2Vhd2VlZEZTX3JkbWFfY29weV9zdGFnaW5nX2FjdGl2ZSAwCiMgSEVMUCBTZWF3ZWVkRlNfcmRtYV9jb3B5X3N0YWdpbmdfYnl0ZXMgQ3VycmVudCBieXRlcyBtYXBwZWQgYWNyb3NzIGluLWZsaWdodCBSRE1BIGNvcHkgcmVnaXN0cmF0aW9ucy4KIyBUWVBFIFNlYXdlZWRGU19yZG1hX2NvcHlfc3RhZ2luZ19ieXRlcyBnYXVnZQpTZWF3ZWVkRlNfcmRtYV9jb3B5X3N0YWdpbmdfYnl0ZXMgMAojIEhFTFAgU2Vhd2VlZEZTX3JkbWFfZWNfcmVnaXN0ZXJlZF9zaGFyZHMgTnVtYmVyIG9mIEVDIGRhdGEgc2hhcmRzIHJlZ2lzdGVyZWQgZm9yIG9uZS1zaWRlZCBSRE1BIHJlYWRzLgojIFRZUEUgU2Vhd2VlZEZTX3JkbWFfZWNfcmVnaXN0ZXJlZF9zaGFyZHMgZ2F1Z2UKU2Vhd2VlZEZTX3JkbWFfZWNfcmVnaXN0ZXJlZF9zaGFyZHMgMAojIEhFTFAgU2Vhd2VlZEZTX3JkbWFfcmVhZF9ieXRlc190b3RhbCBUb3RhbCBieXRlcyBhZHZlcnRpc2VkIGZvciBvbmUtc2lkZWQgUkRNQSByZWFkcy4KIyBUWVBFIFNlYXdlZWRGU19yZG1hX3JlYWRfYnl0ZXNfdG90YWwgY291bnRlcgpTZWF3ZWVkRlNfcmRtYV9yZWFkX2J5dGVzX3RvdGFsIDAKIyBIRUxQIFNlYXdlZWRGU19yZG1hX3JlZ2lzdGVyZWRfdm9sdW1lcyBOdW1iZXIgb2Ygdm9sdW1lcyByZWdpc3RlcmVkIGZvciBvbmUtc2lkZWQgUkRNQSByZWFkcy4KIyBUWVBFIFNlYXdlZWRGU19yZG1hX3JlZ2lzdGVyZWRfdm9sdW1lcyBnYXVnZQpTZWF3ZWVkRlNfcmRtYV9yZWdpc3RlcmVkX3ZvbHVtZXMgMAojIEhFTFAgU2Vhd2VlZEZTX3JkbWFfd3JpdGVfYnl0ZXNfdG90YWwgVG90YWwgYnl0ZXMgcGVyc2lzdGVkIHZpYSBjb21taXR0ZWQgUkRNQSB3cml0ZXMuCiMgVFlQRSBTZWF3ZWVkRlNfcmRtYV93cml0ZV9ieXRlc190b3RhbCBjb3VudGVyClNlYXdlZWRGU19yZG1hX3dyaXRlX2J5dGVzX3RvdGFsIDAKIyBIRUxQIFNlYXdlZWRGU19yZG1hX3dyaXRlX3N0YWdpbmdfYWN0aXZlIEN1cnJlbnQgbnVtYmVyIG9mIGluLWZsaWdodCBSRE1BIHdyaXRlIHN0YWdpbmdzLgojIFRZUEUgU2Vhd2VlZEZTX3JkbWFfd3JpdGVfc3RhZ2luZ19hY3RpdmUgZ2F1Z2UKU2Vhd2VlZEZTX3JkbWFfd3JpdGVfc3RhZ2luZ19hY3RpdmUgMAojIEhFTFAgU2Vhd2VlZEZTX3JkbWFfd3JpdGVfc3RhZ2luZ19ieXRlcyBDdXJyZW50IGJ5dGVzIHBpbm5lZCBhY3Jvc3MgaW4tZmxpZ2h0IFJETUEgd3JpdGUgc3RhZ2luZ3MuCiMgVFlQRSBTZWF3ZWVkRlNfcmRtYV93cml0ZV9zdGFnaW5nX2J5dGVzIGdhdWdlClNlYXdlZWRGU19yZG1hX3dyaXRlX3N0YWdpbmdfYnl0ZXMgMAojIEhFTFAgU2Vhd2VlZEZTX3ZvbHVtZVNlcnZlcl9jb25jdXJyZW50X2Rvd25sb2FkX2xpbWl0IExpbWl0IGZvciB0b3RhbCBjb25jdXJyZW50IGRvd25sb2FkIHNpemUgaW4gYnl0ZXMKIyBUWVBFIFNlYXdlZWRGU192b2x1bWVTZXJ2ZXJfY29uY3VycmVudF9kb3dubG9hZF9saW1pdCBnYXVnZQpTZWF3ZWVkRlNfdm9sdW1lU2VydmVyX2NvbmN1cnJlbnRfZG93bmxvYWRfbGltaXQgMAojIEhFTFAgU2Vhd2VlZEZTX3ZvbHVtZVNlcnZlcl9jb25jdXJyZW50X3VwbG9hZF9saW1pdCBMaW1pdCBmb3IgdG90YWwgY29uY3VycmVudCB1cGxvYWQgc2l6ZSBpbiBieXRlcwojIFRZUEUgU2Vhd2VlZEZTX3ZvbHVtZVNlcnZlcl9jb25jdXJyZW50X3VwbG9hZF9saW1pdCBnYXVnZQpTZWF3ZWVkRlNfdm9sdW1lU2VydmVyX2NvbmN1cnJlbnRfdXBsb2FkX2xpbWl0IDAKIyBIRUxQIFNlYXdlZWRGU192b2x1bWVTZXJ2ZXJfaW5fZmxpZ2h0X2Rvd25sb2FkX3NpemUgSW4gZmxpZ2h0IHRvdGFsIGRvd25sb2FkIHNpemUuCiMgVFlQRSBTZWF3ZWVkRlNfdm9sdW1lU2VydmVyX2luX2ZsaWdodF9kb3dubG9hZF9zaXplIGdhdWdlClNlYXdlZWRGU192b2x1bWVTZXJ2ZXJfaW5fZmxpZ2h0X2Rvd25sb2FkX3NpemUgMAojIEhFTFAgU2Vhd2VlZEZTX3ZvbHVtZVNlcnZlcl9pbl9mbGlnaHRfdXBsb2FkX3NpemUgSW4gZmxpZ2h0IHRvdGFsIHVwbG9hZCBzaXplLgojIFRZUEUgU2Vhd2VlZEZTX3ZvbHVtZVNlcnZlcl9pbl9mbGlnaHRfdXBsb2FkX3NpemUgZ2F1Z2UKU2Vhd2VlZEZTX3ZvbHVtZVNlcnZlcl9pbl9mbGlnaHRfdXBsb2FkX3NpemUgMAojIEhFTFAgU2Vhd2VlZEZTX3ZvbHVtZVNlcnZlcl9tYXhfdm9sdW1lcyBNYXhpbXVtIG51bWJlciBvZiB2b2x1bWVzCiMgVFlQRSBTZWF3ZWVkRlNfdm9sdW1lU2VydmVyX21heF92b2x1bWVzIGdhdWdlClNlYXdlZWRGU192b2x1bWVTZXJ2ZXJfbWF4X3ZvbHVtZXMgMAojIEhFTFAgU2Vhd2VlZEZTX3ZvbHVtZVNlcnZlcl9zdG9yYWdlX2lvX2Vycm9yX3RvdGFsIENvdW50ZXIgb2Ygc3RvcmFnZSByZWFkL3dyaXRlIEVJTyBlcnJvcnMgb24gdm9sdW1lcyBhbmQgRUMgc2hhcmRzLgojIFRZUEUgU2Vhd2VlZEZTX3ZvbHVtZVNlcnZlcl9zdG9yYWdlX2lvX2Vycm9yX3RvdGFsIGNvdW50ZXIKU2Vhd2VlZEZTX3ZvbHVtZVNlcnZlcl9zdG9yYWdlX2lvX2Vycm9yX3RvdGFsIDAKIyBIRUxQIHZvbHVtZV9zZXJ2ZXJfaW5mbGlnaHRfcmVxdWVzdHMgQ3VycmVudCBudW1iZXIgb2YgaW4tZmxpZ2h0IHJlcXVlc3RzCiMgVFlQRSB2b2x1bWVfc2VydmVyX2luZmxpZ2h0X3JlcXVlc3RzIGdhdWdlCnZvbHVtZV9zZXJ2ZXJfaW5mbGlnaHRfcmVxdWVzdHMgMAojIEhFTFAgdm9sdW1lX3NlcnZlcl92b2x1bWVfZmlsZV9jb3VudCBUb3RhbCBudW1iZXIgb2YgZmlsZXMgc3RvcmVkIGFjcm9zcyBhbGwgdm9sdW1lcwojIFRZUEUgdm9sdW1lX3NlcnZlcl92b2x1bWVfZmlsZV9jb3VudCBnYXVnZQp2b2x1bWVfc2VydmVyX3ZvbHVtZV9maWxlX2NvdW50IDAKIyBIRUxQIHZvbHVtZV9zZXJ2ZXJfdm9sdW1lc190b3RhbCBUb3RhbCBudW1iZXIgb2Ygdm9sdW1lcwojIFRZUEUgdm9sdW1lX3NlcnZlcl92b2x1bWVzX3RvdGFsIGdhdWdlCnZvbHVtZV9zZXJ2ZXJfdm9sdW1lc190b3RhbCAwCg==')
        capture=base64.b64decode(fixture['text_b64'],validate=True);self.assertEqual(fixture['source_sha'],'a716580918937dec8ebf0c0fe2d449bfc9152002');self.assertEqual(hashlib.sha256(capture).hexdigest(),fixture['sha256'])
        self.assertEqual(a.probe_module.r2_metrics(a,capture.decode()),[2,3,2,1]);self.reject(lambda:a.probe_module.r2_metrics(a,capture.decode().replace('_total','')),'R2_METRIC_MISSING_OR_DUPLICATE')
        for index in (0,2): records=copy.deepcopy(rows);records[0]['counts'][index]=1;self.reject(lambda:d.recovery(raw(records),text,self.objects,self.identity),'R2_FOREIGN_CLIENT_OR_PERMIT')
        for case in ('zero-row','missing-terminal','duplicate-terminal','wrong-qpn','peer-growth','missing-provider','wrong-server','wrong-child'):
            with self.subTest(case=case):
                records=copy.deepcopy(rows);output=text
                if case=='zero-row':output=output.replace('1 passed','0 passed')
                if case=='missing-terminal':output=output.replace(stdout[1],'')
                if case=='duplicate-terminal':output+='\n'+next(v for v in stdout if v.startswith('R2_TERMINAL '))
                if case=='wrong-qpn':output=output.replace('qp_num=2 ','qp_num=99 ')
                if case=='peer-growth':records[4]['counts'][1]=1
                if case=='missing-provider':records[1]['providers']={}
                if case=='wrong-server':records[1]['actors']=copy.deepcopy(records[1]['actors']);records[1]['actors'][1]['starttime']=88
                if case=='wrong-child':records[1]['actors'][0]['pid']=888
                with self.assertRaises((ValueError,KeyError)):d.recovery(raw(records),output,self.objects,self.identity)
    def test_default_workflow_projection_is_byte_equal(self):
        workflow=(SCRIPTS.parent/'workflows/rdma-softroce-tests.yml').read_text()
        projected=workflow[:workflow.index('\n  tm-connected-diagnostic:')]
        start=projected.index('      diagnostic_profile:');stop=projected.index('concurrency:',start)
        projected=projected[:start]+projected[stop:]
        guard="    if: github.event_name != 'workflow_dispatch' || (github.event.inputs.diagnostic_profile || 'none') == 'none'\n"
        self.assertEqual(projected.count(guard),2);projected=projected.replace(guard,'').rstrip()+'\n'
        self.assertEqual(hashlib.sha256(projected.encode()).hexdigest(),'c3596fa8a4038f5d58cc2648201e1efdd6a8356f28054209dc9452a4f964ee71')
    def test_r2_archive_consumer_and_publisher_boundaries(self):
        reference=dict(run_id=1,attempt=1,workflow_id=2,head_sha='a'*40,actor_id=3,artifact_id=4,name='fixture')
        run=dict(repository=dict(id=5),id=1,run_attempt=1,workflow_id=2,head_sha='a'*40,actor=dict(id=3),conclusion='success')
        artifact=dict(expired=False,workflow_run=dict(id=1),name='fixture',digest='sha256:'+hashlib.sha256(b'ZIP').hexdigest())
        for profile,cap in [('r2-recovery-v1',320*1024*1024),('tm-connected-v1',256*1024*1024)]:
            for size in (cap,cap+1):
                container=Mock();container.infolist.return_value=[a.types.SimpleNamespace(filename='payload.raw',external_attr=0,file_size=size)]
                with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,TM_PROFILE=profile,GITHUB_REPOSITORY_ID='5'),patch.object(a.Actions,'get',side_effect=[run,artifact]),patch.object(a.urllib.request,'build_opener') as redirect,patch.object(a.urllib.request,'urlopen'),patch.object(a,'bounded_read',return_value=b'ZIP'),patch.object(a.zipfile,'ZipFile') as archive:
                    redirect.return_value.open.side_effect=a.urllib.error.HTTPError('https://fixture',302,'redirect',{'Location':'https://fixture'},None);archive.return_value.__enter__.return_value=container
                    call=lambda:a.Actions('seaweedfs/artifactory','inert',pathlib.Path(td)).artifact(reference,pathlib.Path(td)/'unpack',time.monotonic()+10)
                    if size==cap:call();self.assertTrue(container.extract.called)
                    else:self.reject(call,'ARTIFACT_UNPACK_SIZE');self.assertFalse(container.extract.called)
                with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,TM_PROFILE=profile),patch.object(a,'sha',return_value='0'*64):
                    root=pathlib.Path(td)
                    with (root/'payload.raw').open('wb') as f:f.truncate(size-(64+2+len('payload.raw')+1))
                    if profile=='r2-recovery-v1' and size>cap:self.reject(lambda:a.seal(root),'ARTIFACT_UNPACK_SIZE')
                    else:a.seal(root)
        bind=dict(profile='r2-recovery-v1',source_sha='b'*40,ci_sha='c'*40,build_ci_sha='3f4543ab47959d86bea2deefb18193474af5ef58',build_reference=dict(head_sha='3f4543ab47959d86bea2deefb18193474af5ef58'),product_hashes={k:'d'*64 for k in ('loader','server','master')});env=dict(TM_SOURCE_SHA='b'*40,GITHUB_SHA='c'*40)
        a.probe_module.exact_build(a,bind,env);bad=copy.deepcopy(bind);bad['build_ci_sha']='e'*40;self.reject(lambda:a.probe_module.exact_build(a,bad,env),'R2_BUILD_BIND_DRIFT')
    def test_r2_real_service_body_and_role_port_scope(self):
        root=self.dir/'lease';root.mkdir();now=time.monotonic();a.save(root/'clock.json',dict(origin=now,body_deadline=now+20,terminal_deadline=now+30))
        file=self.dir/'bundle.json';a.save(file,dict(bind=dict(profile='r2-recovery-v1',root=str(root),r2_services=dict(master=dict(argv=[sys.executable,'-c','import time;time.sleep(20)',str(root)],env={}))),replacements=dict(ROOT=str(root))))
        a.service(file,'master','up');owner=a.enrollment(root)[0]
        try:
            with patch.object(pathlib.Path,'read_text',return_value='header\n0: 0100007F:B4A3\n'):
                a.ports_free([46240,46241,46242,46244]);self.reject(a.ports_free,'PRE_SPAWN_PORT_COLLISION')
        finally:a.service(file,'master','down');os.waitpid(owner['pid'],0)
        self.assertIsNone(a.proc(owner['pid']))
    def authority(self):
        bind=dict(run_id='fixture-run',ci_sha='a'*40,source_sha='c'*40)
        request=dict(launch_id='e'*24,input_manifest='b'*64)
        env=dict(TM_MANAGER_ACTOR_ID='1',GITHUB_ACTOR_ID='1',GITHUB_SHA='a'*40,TM_SOURCE_SHA='c'*40)
        return bind,request,env
    def test_authority_actor_exact_shas_launch_receipt(self):
        bind,request,env=self.authority();a.authorize(bind,request,env,1)
        for mut,reason in [(lambda b,q,e:e.update(TM_MANAGER_ACTOR_ID=''),'FOREIGN_MANAGER'),(lambda b,q,e:e.update(GITHUB_ACTOR_ID='99'),'FOREIGN_MANAGER'),(lambda b,q,e:b.update(ci_sha='d'*40),'BIND_DRIFT'),(lambda b,q,e:b.update(source_sha='d'*40),'BIND_DRIFT'),(lambda b,q,e:q.update(launch_id=''),'LAUNCH_RECEIPT'),(lambda b,q,e:q.update(input_manifest='drift'),'LAUNCH_RECEIPT'),(lambda b,q,e:b.update(run_id='bad id'),'LOGICAL_RUN_ID')]:
            b,q,e=copy.deepcopy((bind,request,env));mut(b,q,e);self.reject(lambda:a.authorize(b,q,e,1),reason)
        self.reject(lambda:a.authorize(bind,request,env,99),'FOREIGN_MANAGER')
    def test_manifest_external_envelope_and_mutation(self):
        root=self.dir/'input'; root.mkdir(); (root/'bind.json').write_text('{}'); a.seal(root); before=a.sha(root/'manifest.sha256'); a.manifest(root,before)
        external=self.dir/'envelope.json'; external.write_text('{}'); self.assertEqual(before,a.sha(root/'manifest.sha256'))
        (root/'bind.json').write_text('drift'); self.reject(lambda:a.manifest(root,before),'INPUT_BYTE_DRIFT')
        (root/'bind.json').write_text('{}'); (root/'envelope.json').write_text('{}'); a.seal(root); self.reject(lambda:a.manifest(root),'AUTHORITY_CYCLE')
    def test_manifest_paths_and_duplicate(self):
        root=self.dir/'input'; root.mkdir(); (root/'x').write_text('x')
        for name in ('../x','/x','x\\y'):
            (root/'manifest.sha256').write_text('a'*64+'  '+name+'\n'); self.reject(lambda:a.manifest(root),'MANIFEST_PATH')
        (root/'alias').symlink_to(root/'x'); (root/'manifest.sha256').write_text(a.sha(root/'x')+'  alias\n'); self.reject(lambda:a.manifest(root),'MANIFEST_FILE')
    def test_zero_and_multiple_selections(self):
        item=dict(reason='compiler-artifact',target=dict(name='sw_rdma_loader'),profile=dict(test=True),executable='x')
        self.assertEqual(a.select_elf(json.dumps(item).encode(),'sw_rdma_loader',True),pathlib.Path('x'))
        for raw in (b'',(json.dumps(item)+'\n'+json.dumps(item)).encode()): self.reject(lambda:a.select_elf(raw,'sw_rdma_loader',True),'ELF_SELECTION')
        for listing in (b'',((d.ROW+': test\n')*2).encode()): self.reject(lambda:a.exact_row(listing),'ROW_SELECTION')
    def test_build_refuses_clone_fallback_dirty_tree_and_old_source(self):
        source=self.dir/'source';source.mkdir();env=dict(TM_SOURCE_SHA='a'*40)
        with patch.object(a,'command',return_value=('b'*40).encode()):
            self.reject(lambda:a.build(source,self.dir,env),'SOURCE_HEAD_DRIFT')
        with patch.object(a,'command',side_effect=[('a'*40).encode(),b' M changed.rs']):
            self.reject(lambda:a.build(source,self.dir,env),'SOURCE_TREE_DIRTY')
        with patch.object(a,'command',side_effect=[('a'*40).encode(),b'']):
            self.reject(lambda:a.build(source,self.dir,env),'SOURCE_ROW_MISSING')
    def test_decoder_positive_exact_geometry(self):
        raw,stdout=evidence(self.objects,self.identity); result=d.decode(raw,stdout,self.objects,self.identity)
        self.assertEqual(len(result['points']),2430); self.assertEqual(result['native_pd'],'UNAVAILABLE')
    def test_decoder_refusals(self):
        raw,stdout=evidence(self.objects,self.identity)
        for altered,reason in [(raw[:-1],'PARTIAL_BODY'),(raw.replace(b'ibdev=Ok("../../infiniband/siw0")',b'ibdev=Ok("../../infiniband/rxe0")',1),'WRONG_IBDEV'),(raw.replace(b'/dev/infiniband/uverbs0',b'/dev/infiniband/xxxxxx0',1),'UVERBS_FD_MISSING'),(raw.replace(b'INERT_ONLY',b'READ_ERROR',1),'CENSUS_READ_ERROR')]:
            self.reject(lambda:d.decode(altered,stdout,self.objects,self.identity),reason)
        wrong=copy.deepcopy(self.objects); wrong['libsiw']['inode']+=1; self.reject(lambda:d.decode(raw,stdout,wrong,self.identity),'MAPPED_PROVIDER_IDENTITY')
        self.reject(lambda:d.decode(raw,stdout,self.objects,dict(pid=999,starttime=456)),'CHILD_PID_STARTTIME')
        self.reject(lambda:d.decode(raw,stdout.replace('cleanup_error=None','cleanup_error=Some(error)',1),self.objects,self.identity),'CHECKED_CLOSE_FAILED')
        self.reject(lambda:d.decode(raw,stdout.replace('slots=1','slots=2',1),self.objects,self.identity),'TIMING_CORRELATION')
    def caller(self, case='positive'):
        out=self.dir/'output'; out.mkdir(); build=self.dir/'build'; build.mkdir(); helper=self.dir/'helper.py'; helper.write_text(HELPER)
        root=self.dir/'lease'; module=str(pathlib.Path(__file__).resolve())
        client=build/'loader.elf'; client.write_text('#!'+sys.executable+'\nimport sys,json,pathlib,os\nsys.path.insert(0,'+repr(str(SCRIPTS/'tests'))+')\nimport test_rdma_tm_diagnostic as t\nfrom tm_adapter import proc\nobjects=json.loads('+repr(json.dumps(self.objects))+')\nraw,stdout=t.evidence(objects,t.a.proc(os.getpid()))\npathlib.Path(os.environ["TM_CHILD_EVIDENCE_FILE"]).write_bytes(raw)\nprint(stdout)\n'); client.chmod(0o700)
        # Module is loaded under an importlib name; the fake ELF uses t.a rather than sys.modules.
        client.write_text(client.read_text().replace('from tm_adapter import proc\n',''))
        def row(phase,role):return dict(argv=[sys.executable,str(helper),phase,'${ROOT}',role],env={})
        bind=dict(root=str(root),lock_path=str(self.dir/'lock'),run_id='local-inert',whole_seconds=12,reserve_seconds=2,ports=a.PORTS,setup=[row('up','master'),row('up','server')],down=[row('down','server'),row('down','master')],probes=[row('probe','master'),row('probe','server')],test_env={},provider_objects=self.objects)
        if case=='setup-error': bind['setup'][1]['argv']=[sys.executable,'-c','raise SystemExit(7)']
        if case=='probe-timeout': bind['whole_seconds']=2; bind['reserve_seconds']=1; bind['probes'][0]['argv']=[sys.executable,'-c','import time;time.sleep(20)']
        if case=='down-error': bind['down'][0]['argv']=[sys.executable,str(helper),'down','${ROOT}','server']; helper.write_text(HELPER.replace('time.sleep(.05)','time.sleep(.05)\n  raise SystemExit(8)'))
        if case=='terminal-write': bind['probes'][1]['argv']=[sys.executable,'-c','import pathlib,sys;pathlib.Path(sys.argv[1]).mkdir()',str(root/'terminal.json')]
        if case=='child-error': client.write_text('#!'+sys.executable+'\nraise SystemExit(9)\n')
        if case=='test-timeout': bind['whole_seconds']=2;bind['reserve_seconds']=1;client.write_text('#!'+sys.executable+'\nimport time;time.sleep(20)\n')
        if case=='late-evidence': client.write_text(client.read_text()+'import time;time.sleep(20)\n');bind['whole_seconds']=2;bind['reserve_seconds']=1
        return bind,root,out,build
    def test_whole_caller_positive(self):
        bind,root,out,build=self.caller(); self.assertEqual(a.runtime(bind,self.dir,build,out,None,private=True),'RUNTIME_PASS_PUBLICATION_PENDING')
        result=json.loads((out/'runtime-result.json').read_text()); self.assertEqual(len(result['terminal']['owners']),3)
        self.assertTrue((out/'runtime-raw/tm-child.raw').is_file()); self.assertTrue((root/'probe-1.exit.json').is_file())
        self.assertEqual(json.loads((root/'ledger.jsonl').read_text().splitlines()[-1])['event'],'END')
        with open(bind['lock_path'],'a') as lock:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    def test_real_body_budget_consumer_at_exec(self):
        env=a.test_environment({'test_env':{}},self.dir,time.monotonic()+3)
        consumer='import os;ms=int(os.environ["TM_BODY_REMAINING_MS"]);assert 0<ms<=540000;print(ms)'
        self.assertEqual(subprocess.run([sys.executable,'-c',consumer],env=env,capture_output=True).returncode,0)
        broken=dict(env);broken['TM_SHARED_REMAINING_MS']=broken.pop('TM_BODY_REMAINING_MS')
        self.assertNotEqual(subprocess.run([sys.executable,'-c',consumer],env=broken,capture_output=True).returncode,0)
        for delta in (-1,541):self.reject(lambda:a.test_environment({'test_env':{}},self.dir,time.monotonic()+delta),'TEST_BODY_BUDGET')
    def test_production_root_symlink_and_foreign(self):
        real=self.dir/'relocated'/'opt'/'work';real.mkdir(parents=True)
        alias=self.dir/'opt-work';alias.symlink_to(real,target_is_directory=True)
        candidate=alias/'codex03-tm-run';lock=alias/'siw-lab.lock'
        self.assertEqual(a.owned_root(candidate,lock,alias),real/'codex03-tm-run')
        self.assertEqual(a.owned_root(real/'codex03-tm-run',real/'siw-lab.lock',alias),real/'codex03-tm-run')
        for root,bad_lock in [(self.dir/'codex03-tm-foreign',lock),(alias/'codex03-tm-run'/'escape',lock),(candidate,self.dir/'siw-lab.lock')]:
            self.reject(lambda:a.owned_root(root,bad_lock,alias),'OWNED_ROOT_OR_LOCK')
        candidate.symlink_to(self.dir/'foreign',target_is_directory=True)
        self.reject(lambda:a.owned_root(candidate,lock,alias),'OWNED_ROOT_NOT_FRESH')
    def test_whole_caller_setup_error(self): self.failure_case('setup-error',False)
    def test_whole_caller_producer_timeout(self): self.failure_case('probe-timeout',False)
    def test_whole_caller_child_error(self): self.failure_case('child-error',False)
    def test_whole_caller_deadline(self): self.failure_case('test-timeout',False)
    def test_whole_caller_late_raw_retained(self):
        bind,root,out,build=self.failure_case('late-evidence',False); self.assertTrue((out/'runtime-raw/tm-child.raw').is_file())
    def test_whole_caller_down_error_retains_lock(self): self.failure_case('down-error',True)
    def test_whole_caller_terminal_error_retains_lock(self): self.failure_case('terminal-write',True)
    def test_whole_caller_malformed_and_partial_identity(self):
        for index,text in enumerate(('invalid','')):
            # Two independent fixtures exercise the entire caller, including owned down.
            if index: self.tearDown(); self.setUp()
            bind,root,out,build=self.caller()
            bind['probes'][0]['argv']=[sys.executable,'-c','import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(sys.argv[2])',str(root/'server/owner.starttime'),text]
            self.reject(lambda:a.runtime(bind,self.dir,build,out,None,private=True),'RUNTIME_FAILED_OR_UNKNOWN')
            self.assertEqual((root/'state').read_text(),'FAILED_LOCK_RETAINED')
            owners=[json.loads(v) for v in (root/'fixture-owned.jsonl').read_text().splitlines()]
            self.assertTrue(a.census(root,owners)); self.assertNotIn('"event": "END"',(root/'ledger.jsonl').read_text())
            with open(bind['lock_path'],'a') as lock:
                with self.assertRaises(BlockingIOError): fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    def test_whole_caller_cancel_during_setup_and_operation(self):
        for index,stage in enumerate(('setup','operation')):
            if index:self.tearDown();self.setUp()
            bind,root,out,build=self.caller('test-timeout'); bind['whole_seconds']=8;bind['reserve_seconds']=2
            if stage=='setup':bind['setup'][1]['argv']=[sys.executable,'-c','import time;time.sleep(.6)']
            config=self.dir/'config.json';config.write_text(json.dumps(bind))
            script=self.dir/'parent.py';script.write_text('import importlib.util,json,pathlib\ns=importlib.util.spec_from_file_location("a",'+repr(str(SCRIPTS/'rdma-tm-diagnostic.py'))+');a=importlib.util.module_from_spec(s);s.loader.exec_module(a)\na.runtime(json.loads(pathlib.Path('+repr(str(config))+').read_text()),pathlib.Path('+repr(str(self.dir))+'),pathlib.Path('+repr(str(build))+'),pathlib.Path('+repr(str(out))+'),None,private=True)\n')
            parent=subprocess.Popen([sys.executable,str(script)],start_new_session=True)
            limit=time.monotonic()+5; target=root/('fixture-owned.jsonl' if stage=='setup' else 'client.identity.json')
            while not target.exists() and time.monotonic()<limit:time.sleep(.02)
            self.assertTrue(target.exists());os.killpg(parent.pid,signal.SIGTERM);parent.wait(timeout=1)
            while not (root/'terminal.json').is_file() and time.monotonic()<limit:time.sleep(.02)
            self.assertTrue((root/'terminal.json').is_file())
            terminal=json.loads((root/'terminal.json').read_text());self.assertEqual(terminal['status'],'FAIL');self.assertIn('CALLER_LOST',terminal['primary_setup_error'])
            self.assertEqual(a.census(root,terminal['owners']),[]);self.assertFalse((out/'runtime-result.json').exists())
    def test_command_deadline_and_ports_collision(self):
        self.reject(lambda:a.command([sys.executable,'-c','raise SystemExit(0)'],self.dir,'expired',time.monotonic()-1),'ABSOLUTE_DEADLINE')
        with patch.object(pathlib.Path,'read_text',return_value='header\n0: 00000000:B4A0 rest\n'):
            self.reject(a.ports_free,'PRE_SPAWN_PORT_COLLISION')
    def test_credentials_are_not_forwarded(self):
        with patch.dict(os.environ,{'GH_TOKEN':'INERT_NOT_A_TOKEN','EXTRA_SECRET':'INERT'}):
            safe=a.service_env({'RUN':'owned'});self.assertNotIn('GH_TOKEN',safe);self.assertNotIn('EXTRA_SECRET',safe)
        self.reject(lambda:a.service_env({'GH_TOKEN':'INERT'}),'SERVICE_CREDENTIAL_OVERRIDE')
    def test_deadline_read_preserves_late_chunk(self):
        class Late:
            def read1(self,n):time.sleep(.06);return b'late-refusal-evidence'
        raw=self.dir/'late.raw'
        self.reject(lambda:a.bounded_read(Late(),time.monotonic()+.03,100,raw),'API_ABSOLUTE_DEADLINE')
        self.assertTrue(raw.is_file())
        class LateClock:
            def read1(self,n):return b'late-refusal-evidence'
        with patch.object(a.time,'monotonic',side_effect=[1,1,3]):
            self.reject(lambda:a.bounded_read(LateClock(),2,100,raw),'API_SIZE_OR_DEADLINE')
        self.assertEqual(raw.read_bytes(),b'late-refusal-evidence')
    def test_artifact_association_redirect_and_zip(self):
        reference=dict(run_id=1,attempt=2,workflow_id=3,head_sha='a'*40,actor_id='4',artifact_id=5,name='fixture')
        run=dict(repository=dict(id=6),id=1,run_attempt=2,workflow_id=3,head_sha='a'*40,actor=dict(id=4),conclusion='success')
        for field,value in [('run_attempt',99),('head_sha','b'*40),('conclusion','cancelled'),('repository',dict(id=99)),('actor',dict(id=99))]:
            api=a.Actions('fixture/repo','INERT',self.dir)
            with patch.dict(os.environ,{'GITHUB_REPOSITORY_ID':'6'}),patch.object(api,'get',return_value=dict(run,**{field:value})):
                self.reject(lambda:api.artifact(reference,self.dir/'refused',time.monotonic()+1),'ARTIFACT_RUN_ASSOCIATION')
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:z.writestr('bind.json','{}')
        payload=stream.getvalue();artifact=dict(expired=False,workflow_run=dict(id=1),name='fixture',digest='sha256:'+hashlib.sha256(payload).hexdigest())
        api=a.Actions('fixture/repo','INERT',self.dir)
        opener=unittest.mock.Mock();opener.open.side_effect=a.urllib.error.HTTPError('url',302,'redirect',{'Location':'https://signed.invalid/object'},None)
        response=io.BytesIO(payload);response.read1=response.read
        with patch.dict(os.environ,{'GITHUB_REPOSITORY_ID':'6'}),patch.object(api,'get',side_effect=[run,artifact]),patch.object(a.urllib.request,'build_opener',return_value=opener),patch.object(a.urllib.request,'urlopen',return_value=response) as download:
            api.artifact(reference,self.dir/'download',time.monotonic()+2)
        self.assertEqual(download.call_args.args[0],'https://signed.invalid/object');self.assertEqual((self.dir/'download/bind.json').read_text(),'{}')
    def test_unbound_authority_refuses_before_artifact_or_services(self):
        event=self.dir/'event.json';event.write_text(json.dumps(dict(inputs=dict(diagnostic_objects=json.dumps(dict(launch_id='x',input_manifest='y'))),sender=dict(id=1))))
        env=dict(GITHUB_REPOSITORY='fixture/repo',GH_TOKEN='INERT',GITHUB_EVENT_PATH=str(event))
        with patch.object(a.Actions,'artifact') as download:
            self.reject(lambda:a.run_phase(self.dir,env),'FOREIGN_MANAGER_DISPATCH');download.assert_not_called()
    def test_ci_wait_uses_actual_jobs_and_original_deadline(self):
        api=a.Actions('fixture/repo','INERT',self.dir)
        busy=dict(total_count=1,workflow_runs=[dict(id=9)])
        jobs=dict(total_count=1,jobs=[dict(status='in_progress',runner_name='tp01-second')])
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'1','RUNNER_NAME':'tp01-first'}),patch.object(api,'get',side_effect=[busy,jobs]):
            self.reject(lambda:a.wait_ci(api,dict(runner_names=['tp01-first','tp01-second']),self.dir,time.monotonic()+.1),'CI_ACTIVE_SETUP_BLOCKED')
        row=json.loads((self.dir/'runner-job-states.json').read_text());self.assertEqual(len(row['foreign_active_jobs']),1)
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'1','RUNNER_NAME':'tp01-first'}),patch.object(api,'get',return_value=dict(total_count=0,workflow_runs=[])) as get:
            a.wait_ci(api,dict(runner_names=['tp01-first','tp01-second']),self.dir,time.monotonic()+1)
            self.assertNotIn('/runners',get.call_args.args[0])
    def test_fetch_policy_refuses_non_crates_source_and_overrides(self):
        source=self.dir/'source'
        for name in ('enterprise/rust','enterprise/seaweed-volume'):
            p=source/name;p.mkdir(parents=True);(p/'Cargo.lock').write_text('source = "registry+https://github.com/rust-lang/crates.io-index"\n')
        env=dict(CARGO_HOME=str(self.dir/'cargo'));self.assertEqual(a.fetch_policy(source,env)['CARGO_NET_RETRY'],'0')
        self.reject(lambda:a.fetch_policy(source,dict(env,CARGO_REGISTRIES_OTHER_INDEX='https://foreign.invalid')),'FETCH_REGISTRY_OVERRIDE')
        (source/'enterprise/rust/Cargo.lock').write_text('source = "git+https://foreign.invalid"\n')
        self.reject(lambda:a.fetch_policy(source,env),'FETCH_NON_CRATES_IO_SOURCE')
    def test_whole_caller_cancel_during_teardown(self):
        bind,root,out,build=self.caller();helper=self.dir/'helper.py'
        helper.write_text(HELPER.replace("elif phase=='down':","elif phase=='down':\n (root/'down-entered').write_text('yes');time.sleep(.6)"))
        config=self.dir/'config.json';config.write_text(json.dumps(bind))
        script=self.dir/'parent.py';script.write_text('import importlib.util,json,pathlib\ns=importlib.util.spec_from_file_location("a",'+repr(str(SCRIPTS/'rdma-tm-diagnostic.py'))+');a=importlib.util.module_from_spec(s);s.loader.exec_module(a)\na.runtime(json.loads(pathlib.Path('+repr(str(config))+').read_text()),pathlib.Path('+repr(str(self.dir))+'),pathlib.Path('+repr(str(build))+'),pathlib.Path('+repr(str(out))+'),None,private=True)\n')
        parent=subprocess.Popen([sys.executable,str(script)],start_new_session=True);limit=time.monotonic()+6
        while not (root/'down-entered').exists() and time.monotonic()<limit:time.sleep(.02)
        self.assertTrue((root/'down-entered').exists());os.killpg(parent.pid,signal.SIGTERM);parent.wait(timeout=1)
        while not (root/'terminal.json').is_file() and time.monotonic()<limit:time.sleep(.02)
        self.assertTrue((root/'terminal.json').is_file());terminal=json.loads((root/'terminal.json').read_text())
        self.assertEqual(a.census(root,terminal['owners']),[]);self.assertFalse((out/'runtime-result.json').exists())
        # A runtime terminal is not final Actions publication; a lost caller cannot publish PASS.
    def failure_case(self,case,retained):
        bind,root,out,build=self.caller(case); self.reject(lambda:a.runtime(bind,self.dir,build,out,None,private=True),'RUNTIME_FAILED_OR_UNKNOWN')
        result=json.loads((out/'runtime-result.json').read_text()); self.assertEqual(result['state'],'FAILED_OR_UNKNOWN')
        if retained:
            self.assertEqual((root/'state').read_text(),'FAILED_LOCK_RETAINED')
            with open(bind['lock_path'],'a') as lock:
                with self.assertRaises(BlockingIOError):fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertNotIn('"event": "END"',(root/'ledger.jsonl').read_text())
        else:
            with open(bind['lock_path'],'a') as lock:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertTrue(result['primary_error'])
        return bind,root,out,build
    def test_upload_missing_foreign_and_success(self):
        env=dict(GITHUB_REPOSITORY='fixture/repo',GH_TOKEN='fixture-not-secret',GITHUB_RUN_ID='1'); row=dict(workflow_run=dict(id=1),expired=False,digest='sha256:'+'a'*64)
        with patch.object(a.Actions,'get',return_value=row):a.upload_proof(self.dir,env,'2','a'*64)
        for altered in [dict(row,expired=True),dict(row,workflow_run=dict(id=99)),dict(row,digest='sha256:'+'b'*64)]:
            with patch.object(a.Actions,'get',return_value=altered):self.reject(lambda:a.upload_proof(self.dir,env,'2','a'*64),'UPLOAD_FAILED_OR_FOREIGN')
        with patch.object(a.Actions,'get',side_effect=OSError('publication unavailable')):
            with self.assertRaises(OSError):a.upload_proof(self.dir,env,'2','a'*64)

class LockedFetchControls(unittest.TestCase):
    def test_actual_fetch_branch_serial_exit_deadline_and_hash_refusals(self):
        import ast
        tree=ast.parse((SCRIPTS/'rdma-tm-diagnostic.py').read_text())
        build=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='build')
        branch=next(n for n in build.body if isinstance(n,ast.If) and 'TM_FETCH_AUTHORITY' in ast.unparse(n.test))
        entry=compile(ast.fix_missing_locations(ast.Module(body=[branch],type_ignores=[])),'actual-build-fetch-branch','exec')
        fake='''#!/usr/bin/python3
import os,sys,json,pathlib,time
assert os.environ['CARGO_NET_RETRY']=='0' and os.environ['CARGO_NET_OFFLINE']=='false'
assert os.environ['CARGO_REGISTRIES_CRATES_IO_INDEX']=='sparse+https://index.crates.io/'
assert sys.argv[1:4]==['fetch','--locked','--target'] and sys.argv[4]=='x86_64-unknown-linux-gnu'
manifest=pathlib.Path(sys.argv[sys.argv.index('--manifest-path')+1]);case=os.environ['FETCH_CONTROL_CASE']
print(json.dumps(dict(workspace=str(manifest.parent),case=case)),flush=True)
if case in ('drift','error-drift'):manifest.with_name('Cargo.lock').write_text('MUTATED')
if case=='deadline':time.sleep(10)
raise SystemExit(7 if case in ('error','error-drift') else 0)
'''
        for case in ('positive','error','deadline','drift','error-drift','wrong-authority','missing-build-authority'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as td:
                root=pathlib.Path(td);source=root/'source';out=root/'out';out.mkdir();bin=root/'bin';bin.mkdir();cargo=bin/'cargo';cargo.write_text(fake);cargo.chmod(0o700)
                for name in ('enterprise/rust','enterprise/seaweed-volume'):
                    directory=source/name;directory.mkdir(parents=True);(directory/'Cargo.toml').write_text('[workspace]\n');(directory/'Cargo.lock').write_text('source = "registry+https://github.com/rust-lang/crates.io-index"\n')
                snapshots={str(f.relative_to(source)):a.sha(f) for f in source.rglob('Cargo.*')}
                env=dict(os.environ,PATH=str(bin)+':'+os.environ['PATH'],CARGO_HOME=str(root/'cargo'),TM_SOURCE_SHA='a'*40,TM_FETCH_AUTHORITY='FETCH_LOCKED '+'a'*40,TM_BUILD_AUTHORITY='BUILD_ONLY '+'a'*40,FETCH_CONTROL_CASE=case)
                if case=='wrong-authority':env['TM_FETCH_AUTHORITY']='FETCH_LOCKED '+'b'*40
                if case=='missing-build-authority':env['TM_BUILD_AUTHORITY']=''
                calls=[];actual=a.command
                def bounded(*args,**kwargs):
                    argv,output,label,deadline=args;calls.append(dict(argv=argv,deadline_seconds=deadline-time.monotonic(),offline=kwargs['env']['CARGO_NET_OFFLINE']))
                    self.assertLessEqual(calls[-1]['deadline_seconds'],300);self.assertGreater(calls[-1]['deadline_seconds'],299)
                    return actual(argv,output,label,min(deadline,time.monotonic()+.15) if case=='deadline' else deadline,**kwargs)
                namespace=dict(a.__dict__,source=source,out=out,env=env,safe=dict(env,CARGO_NET_OFFLINE='true'),snapshots=snapshots)
                error=None
                with patch.object(a,'command',side_effect=bounded):
                    try:exec(entry,namespace)
                    except BaseException as failure:error=failure
                records=sorted([json.loads(f.read_text()) for f in out.glob('*.fetch.json')],key=lambda row:row['invocation_ordinal'])
                if case=='positive':
                    self.assertIsNone(error);self.assertEqual([r['workspace'] for r in records],['enterprise/rust','enterprise/seaweed-volume']);self.assertEqual(len(calls),2);self.assertGreaterEqual(records[1]['started'],records[0]['ended'])
                elif case in ('wrong-authority','missing-build-authority'):
                    self.assertIn('FETCH_AUTHORITY',str(error));self.assertFalse(calls);self.assertFalse(records);continue
                else:self.assertIsNotNone(error);self.assertEqual(len(calls),1);self.assertEqual(len(records),1)
                for record in records:
                    self.assertEqual(record['state'],'PASS' if case=='positive' else 'REFUSED');self.assertEqual(record['sparse_index_git_revision'],'NOT_APPLICABLE');self.assertGreaterEqual(record['duration_seconds'],0)
                    self.assertEqual(record['hashes_unchanged'],case not in ('drift','error-drift'));self.assertIsNotNone(record['command_exit'])
                if case in ('drift','error-drift'):self.assertIn('FETCH_MANIFEST_DRIFT',str(error))
                if case=='error-drift':self.assertIn('COMMAND_REFUSED',records[0]['primary_refusal'])
                if case=='deadline':self.assertTrue(records[0]['command_exit']['cleanup']['reaped']);self.assertEqual(records[0]['command_exit']['exit'],124)
class WorkflowActionlint(unittest.TestCase):
    """Mandatory CI-box workflow parser; missing pinned tool is a failure."""
    def test_pinned_actionlint_and_runner_context_refusal(self):
        tool=os.environ['TM_ACTIONLINT']
        version=subprocess.run([tool,'-version'],capture_output=True,text=True,check=True)
        self.assertTrue(version.stdout.startswith('1.7.12\n'),version.stdout)
        workflow=SCRIPTS.parents[0]/'workflows/rdma-softroce-tests.yml'
        with tempfile.TemporaryDirectory() as directory:
            config=pathlib.Path(directory)/'actionlint.yaml'
            config.write_text('self-hosted-runner:\n  labels: [tp01]\n')
            argv=[tool,'-config-file',str(config),'-shellcheck=','-pyflakes=']
            result=subprocess.run([*argv,str(workflow)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(result.stdout+result.stderr,'')
            invalid=pathlib.Path(directory)/'invalid.yml'
            text=workflow.read_text().replace('    env:\n      TM_SOURCE_SHA:', '    env:\n      RUNNER_ENVIRONMENT: ${{ runner.environment }}\n      TM_SOURCE_SHA:')
            self.assertNotEqual(text,workflow.read_text())
            invalid.write_text(text)
            refused=subprocess.run([*argv,str(invalid)],capture_output=True,text=True)
            self.assertNotEqual(refused.returncode,0)
            self.assertIn('context \"runner\" is not allowed here',refused.stdout+refused.stderr)

if __name__=='__main__': unittest.main()


class BuildRunnerAdmission(unittest.TestCase):
    def test_missing_lookup_names_and_found_versions_before_mutation(self):
        names=('whoami','rustc','cargo','go','cc','readelf','pkg-config')
        for missing in (*names,'rustc,cargo'):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as td:
                base=pathlib.Path(td);workspace=base/'workspace';workspace.mkdir();out=base/'raw';out.mkdir();seen=[]
                env=dict(os.environ,GITHUB_WORKSPACE=str(workspace),GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1',RUNNER_NAME='inert-fixture',TM_SOURCE_SHA='a'*40,TM_BUILD_AUTHORITY='BUILD_ONLY '+'a'*40,TM_MANAGER_ACTOR_ID='6647175',GITHUB_ACTOR_ID='6647175')
                missing_set=set(missing.split(','))
                def lookup(name,**kwargs):return None if name in missing_set else '/inert/'+name
                def version(argv,*args,**kwargs):
                    name=pathlib.Path(argv[0]).name;seen.append(name)
                    return b'go version go1.26.6 linux/amd64' if name=='go' else ('FOUND_VERSION_'+name).encode()
                with patch.object(a.shutil,'which',side_effect=lookup),patch.object(a,'command',side_effect=version):
                    with self.assertRaisesRegex(ValueError,'BUILD_TOOLCHAIN_MISSING_'):a.runner_preflight(out,env)
                row=json.loads((out/'runner-preflight.json').read_text())
                self.assertEqual(row['missing_tools'],[name for name in names if name in missing_set])
                self.assertEqual(row['error_code'],'BUILD_TOOLCHAIN_MISSING_'+','.join(row['missing_tools']))
                self.assertEqual(seen,[name for name in names if name not in missing_set])
                for name in names:
                    tool=row['tools'][name]
                    if name in missing_set:
                        self.assertIsNone(tool['path']);self.assertEqual(tool['state'],'MISSING');self.assertEqual(tool['error_code'],'BUILD_TOOLCHAIN_MISSING_'+name)
                    else:
                        self.assertEqual(tool['state'],'VERSION_FOUND');self.assertTrue(tool['version_raw']);self.assertEqual(tool['raw_file'],'runner-'+name+'.stdout.raw')
                self.assertFalse((workspace/'codex03-tm-build-123-1').exists());self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.0,0.0))

    def test_actual_preflight_and_refusals(self):
        import shutil
        for case in ('canonical','workspace-alias','foreign-owner','root-alias','collision','permission','missing-tool','wrong-go','deadline','authority'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as td:
                base=pathlib.Path(td);workspace=base/'workspace';workspace.mkdir();alias=base/'alias';alias.symlink_to(workspace);out=base/'raw';out.mkdir()
                env=dict(os.environ,GITHUB_WORKSPACE=str(alias if case=='workspace-alias' else workspace),GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1',RUNNER_NAME='inert-fixture',TM_SOURCE_SHA='a'*40,TM_BUILD_AUTHORITY='BUILD_ONLY '+'a'*40,TM_MANAGER_ACTOR_ID='6647175',GITHUB_ACTOR_ID='6647175')
                target=workspace/'codex03-tm-build-123-1'
                if case=='root-alias':target.symlink_to(base,target_is_directory=True)
                if case=='collision':target.mkdir()
                if case=='authority':env['GITHUB_ACTOR_ID']='foreign'
                original_stat=pathlib.Path.stat;original_mkdir=pathlib.Path.mkdir;original_timer=signal.setitimer
                def stat(path,*args,**kwargs):
                    row=original_stat(path,*args,**kwargs)
                    if case=='foreign-owner' and path==workspace:
                        values=list(row);values[4]=os.getuid()+1;return os.stat_result(values)
                    return row
                def mkdir(path,*args,**kwargs):
                    if case=='permission' and path==target:raise PermissionError('fixture denied')
                    return original_mkdir(path,*args,**kwargs)
                def command(argv,*args,**kwargs):
                    if case=='deadline':time.sleep(.1)
                    return b'go version go1.25.0 linux/amd64' if case=='wrong-go' else b'go version go1.26.6 linux/amd64'
                with patch.object(pathlib.Path,'stat',stat),patch.object(pathlib.Path,'mkdir',mkdir),patch.object(a.shutil,'which',return_value=None if case=='missing-tool' else '/inert/tool'),patch.object(a,'command',side_effect=command),patch.object(signal,'setitimer',side_effect=lambda kind,seconds:original_timer(kind,.03 if case=='deadline' and seconds else seconds)):
                    if case in ('canonical','workspace-alias'):a.runner_preflight(out,env)
                    else:
                        with self.assertRaises((ValueError,PermissionError)):a.runner_preflight(out,env)
                record=json.loads((out/'runner-preflight.json').read_text())
                self.assertEqual(record['state'],'PASS' if case in ('canonical','workspace-alias') else 'FAIL')
                if record['state']=='PASS':
                    self.assertEqual(record['root'],str(target));self.assertEqual(record['root_identity']['uid'],os.getuid());self.assertEqual(record['capacity'],'CAPACITY_UNQUALIFIED');self.assertFalse((target/'admission-probe').exists())
                    self.assertEqual(a.admitted_build_root(out,env),target)
                    record['root_identity']['inode']+=1;a.save(out/'runner-preflight.json',record)
                    with self.assertRaisesRegex(ValueError,'BUILD_ROOT_IDENTITY_DRIFT'):a.admitted_build_root(out,env)
                else:self.assertIn('error_code',record)
                self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.0,0.0))

    def test_build_root_drift_and_containment(self):
        with tempfile.TemporaryDirectory() as td:
            env=dict(GITHUB_WORKSPACE=td,GITHUB_RUN_ID='../foreign',GITHUB_RUN_ATTEMPT='1')
            with self.assertRaisesRegex(ValueError,'BUILD_RUN_IDENTITY'):a.runner_build_root(env)
            env['GITHUB_RUN_ID']='1';target=a.runner_build_root(env);target.mkdir();target.rmdir();target.symlink_to(pathlib.Path(td),target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'BUILD_ROOT_ALIAS'):a.runner_build_root(env,fresh=False)


class PreflightRealAlarmCleanup(unittest.TestCase):
    def test_alarm_stops_and_reaps_real_owned_child(self):
        original_spawn=subprocess.Popen;original_timer=signal.setitimer;children=[]
        with tempfile.TemporaryDirectory() as td:
            base=pathlib.Path(td);workspace=base/'workspace';workspace.mkdir();out=base/'raw';out.mkdir()
            env=dict(os.environ,GITHUB_WORKSPACE=str(workspace),GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1',RUNNER_NAME='inert-real-child',TM_SOURCE_SHA='a'*40,TM_BUILD_AUTHORITY='BUILD_ONLY '+'a'*40,TM_MANAGER_ACTOR_ID='6647175',GITHUB_ACTOR_ID='6647175')
            def spawn(argv,**kwargs):
                child=original_spawn([sys.executable,'-u','-c',"import time,signal;signal.signal(signal.SIGTERM,signal.SIG_IGN);print('REAL_CHILD_STARTED',flush=True);time.sleep(5)"],**kwargs);children.append(child);return child
            try:
                with patch.object(a.shutil,'which',return_value='/inert/tool'),patch.object(a.subprocess,'Popen',side_effect=spawn),patch.object(signal,'setitimer',side_effect=lambda kind,seconds:original_timer(kind,.15 if seconds else 0)):
                    with self.assertRaisesRegex(ValueError,'RUNNER_PREFLIGHT_DEADLINE'):a.runner_preflight(out,env)
                self.assertEqual(len(children),1);child=children[0]
                self.assertIsNotNone(child.returncode);self.assertIsNone(a.proc(child.pid))
                with self.assertRaises(ChildProcessError):os.waitpid(child.pid,os.WNOHANG)
                record=json.loads((out/'runner-whoami.exit.json').read_text());self.assertIn('RUNNER_PREFLIGHT_DEADLINE',record['primary_refusal']);self.assertTrue(record['cleanup']['checked']);self.assertTrue(record['cleanup']['reaped']);self.assertEqual(record['cleanup']['actual_exit'],-signal.SIGKILL)
                self.assertIn(b'REAL_CHILD_STARTED',(out/'runner-whoami.stdout.raw').read_bytes());self.assertTrue((out/'runner-whoami.stderr.raw').is_file());self.assertEqual(json.loads((out/'runner-preflight.json').read_text())['error_code'],'RUNNER_PREFLIGHT_DEADLINE')
                if os.environ.get('TM_ALARM_CONTROL_RECEIPT'):
                    import shutil
                    retained=pathlib.Path(os.environ['TM_ALARM_CONTROL_RECEIPT']);retained.mkdir(parents=True,exist_ok=True)
                    for path in out.iterdir():shutil.copyfile(path,retained/path.name)
                    a.save(retained/'child-proof.json',dict(pid=child.pid,identity=record['cleanup']['identity'],absent=a.proc(child.pid) is None,reaped=True,grade='LOCAL_REAL_CHILD_CONTROL_NOT_HOST',alarm_seconds=.15,admission_seconds=20,term_ignored=True))
            finally:
                for child in children:
                    if child.poll() is None:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=1)

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
from unittest.mock import patch

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
    def test_default_workflow_projection_is_byte_equal(self):
        workflow=(SCRIPTS.parent/'workflows/rdma-softroce-tests.yml').read_text()
        projected=workflow[:workflow.index('\n  tm-connected-diagnostic:')]
        start=projected.index('      diagnostic_profile:');stop=projected.index('concurrency:',start)
        projected=projected[:start]+projected[stop:]
        guard="    if: github.event_name != 'workflow_dispatch' || (github.event.inputs.diagnostic_profile || 'none') == 'none'\n"
        self.assertEqual(projected.count(guard),2);projected=projected.replace(guard,'').rstrip()+'\n'
        self.assertEqual(hashlib.sha256(projected.encode()).hexdigest(),'c3596fa8a4038f5d58cc2648201e1efdd6a8356f28054209dc9452a4f964ee71')
    def authority(self):
        bind=dict(run_id='fixture-run')
        trust=dict(manager_actor_id='1',publication_workflow_id='2',publication_head_sha='a'*40,dispatch_actor_id=1,publication=dict(actor=dict(id=1),workflow_id=2,head_sha='a'*40),envelope_id='e'*24,input_manifest='b'*64)
        envelope=dict(workspace='seaweed',channelId='6a9c32d9c96e4d19d8100d51',sender='1207574175858819073',id='e'*24,text='LAUNCH '+'b'*64+' fixture-run')
        return bind,envelope,trust
    def test_authority_cycle_drift_foreign(self):
        bind,envelope,trust=self.authority(); a.authorize(bind,envelope,trust)
        full=dict(envelope,text='@codex03 12:54Z '+envelope['text']+' — quote in START; no retry.')
        a.authorize(bind,full,trust)
        self.reject(lambda:a.authorize(bind,dict(full,text=full['text']+' LAUNCH '+'b'*64),trust),'LAUNCH_CROSS_REFERENCE')
        for mut,reason in [(lambda b,e,t:b.update(envelope=e),'AUTHORITY_CYCLE'),(lambda b,e,t:t.update(manager_actor_id=''),'AUTHORITY_UNBOUND'),(lambda b,e,t:t.update(dispatch_actor_id=99),'FOREIGN_MANAGER'),(lambda b,e,t:t['publication']['actor'].update(id=99),'FOREIGN_AUTHORITY'),(lambda b,e,t:t['publication'].update(head_sha='c'*40),'FOREIGN_AUTHORITY'),(lambda b,e,t:e.update(sender='99'),'FOREIGN_HULY'),(lambda b,e,t:e.update(text='LAUNCH foreign'),'LAUNCH_CROSS_REFERENCE'),(lambda b,e,t:b.update(run_id='foreign'),'LAUNCH_CROSS_REFERENCE')]:
            b,e,t=copy.deepcopy((bind,envelope,trust)); mut(b,e,t); self.reject(lambda:a.authorize(b,e,t),reason)
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
        event=self.dir/'event.json';event.write_text(json.dumps(dict(inputs=dict(diagnostic_objects=json.dumps(dict(envelope_id='x',input_manifest='y'))),sender=dict(id=1))))
        env=dict(GITHUB_REPOSITORY='fixture/repo',GH_TOKEN='INERT',GITHUB_EVENT_PATH=str(event))
        with patch.object(a.Actions,'artifact') as download:
            self.reject(lambda:a.run_phase(self.dir,env),'AUTHORITY_UNBOUND');download.assert_not_called()
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

if __name__=='__main__': unittest.main()

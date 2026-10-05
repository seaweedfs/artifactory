"""Local boundary controls; no fabricated fixture is a siw observation."""
import copy,importlib.util,json,os,pathlib,signal,subprocess,sys,tempfile,time,unittest,types
from unittest.mock import patch
scripts=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('tm_probe_adapter',scripts/'rdma-tm-diagnostic.py');a=importlib.util.module_from_spec(spec);sys.modules[spec.name]=a;spec.loader.exec_module(a);p=a.probe_module

def bind():
    return dict(source_sha=p.MONO_SHA,ci_sha='1'*40,build_ci_sha=p.BUILD_SHA,build_reference=copy.deepcopy(p.BUILD_REF),product_hashes=copy.deepcopy(p.PRODUCTS),build_json_sha256=p.BUILD_JSON,list_sha256=p.LIST_SHA)

class ProbeControls(unittest.TestCase):
    def tearDown(self):signal.setitimer(signal.ITIMER_REAL,0)
    def test_exact_prior_build_and_successor_are_distinct(self):
        b=bind();env=dict(TM_SOURCE_SHA=p.MONO_SHA,GITHUB_SHA='1'*40);p.exact_build(a,b,env)
        for key,value in [('build_ci_sha','0'*40),('ci_sha',p.BUILD_SHA),('source_sha','0'*40),('build_json_sha256','0'*64),('list_sha256','0'*64)]:
            v=copy.deepcopy(b);v[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):p.exact_build(a,v,env)
        for key in p.BUILD_REF:
            v=copy.deepcopy(b);v['build_reference'][key]='FOREIGN'
            with self.subTest(association=key),self.assertRaisesRegex(ValueError,'UNAPPROVED'):p.exact_build(a,v,env)
        for label in p.PRODUCTS:
            v=copy.deepcopy(b);v['product_hashes'][label]='0'*64
            with self.subTest(product=label),self.assertRaisesRegex(ValueError,'PROVENANCE'):p.exact_build(a,v,env)
    def test_routing_probe_has_no_default_opt_in(self):
        params=dict(diagnostic_profile='tm-connected-v1',diagnostic_phase='probe',mono_sha=p.MONO_SHA,runner='tp01')
        self.assertEqual(a.routing('workflow_dispatch',params,'self-hosted'),'DIAGNOSTIC')
        self.assertEqual(a.routing('repository_dispatch',params,'self-hosted'),'DEFAULT')
        params['diagnostic_profile']='none';self.assertEqual(a.routing('workflow_dispatch',params,'self-hosted'),'DEFAULT')
        params['diagnostic_profile']='tm-connected-v1';params['diagnostic_phase']='foreign'
        with self.assertRaisesRegex(ValueError,'PHASE'):a.routing('workflow_dispatch',params,'self-hosted')
    def test_actual_sysfs_shape_unique_missing_ambiguous_gid(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);g=root/'siw0/ports/1/gids';n=root/'siw0/ports/1/gid_attrs/ndevs';g.mkdir(parents=True);n.mkdir(parents=True)
            (g/'0').write_text('0000:0000:0000:0000:0000:ffff:c633:6401');(n/'0').write_text('siwci')
            links='link siw0/1 state ACTIVE physical_state LINK_UP netdev siwci\n';ip=[dict(ifname='siwci',addr_info=[dict(family='inet',local='198.51.100.1')])]
            row=p.network(a,root,links,ip);self.assertEqual(row['ip'],'198.51.100.1');self.assertEqual(row['gid_index'],0);self.assertEqual(len(row['gid_rows']),1)
            with self.assertRaisesRegex(ValueError,'MISSING_OR_AMBIGUOUS'):p.network(a,root,links,[])
            (g/'1').write_text((g/'0').read_text());(n/'1').write_text('siwci')
            with self.assertRaisesRegex(ValueError,'MISSING_OR_AMBIGUOUS'):p.network(a,root,links,ip)
            with self.assertRaisesRegex(ValueError,'NETDEV_AMBIGUOUS'):p.network(a,root,links+'link siw0/1 netdev foreign\n',ip)
            (n/'0').unlink()
            with self.assertRaises(FileNotFoundError):p.network(a,root,links,ip,root)
            self.assertIn('gid',json.loads((root/'sysfs-gid-observations.json').read_text())['rows'][0])
    def test_real_object_identity_detects_inode_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            file=pathlib.Path(directory)/'provider';file.write_bytes(b'fixture-object-not-provider');row=p.object_identity(a,file)
            self.assertEqual(row['inode'],file.stat().st_ino);self.assertEqual(row['sha256'],a.sha(file))
            original=a.sha
            def drifting(f):
                digest=original(f);other=file.with_suffix('.new');other.write_bytes(b'changed');other.replace(file);return digest
            with patch.object(a,'sha',drifting),self.assertRaisesRegex(ValueError,'OBJECT_DRIFT'):p.object_identity(a,file)
    def test_actual_uid_root_writability_alias_foreign_refusals(self):
        with tempfile.TemporaryDirectory() as directory:
            base=pathlib.Path(directory).resolve();out=base/'output';out.mkdir();link=base/'alias';link.symlink_to(base)
            b=dict(root=str(link/'codex03-tm-probe'),lock_path=str(link/'siw-lab.lock'))
            r=p.root_access(a,b,out,time.monotonic()+10,str(link));self.assertEqual(r['uid'],os.getuid());self.assertTrue(r['cleanup']['scratch_absent']);self.assertFalse(pathlib.Path(b['root']).exists())
            b['root']=str(base.parent/'foreign')
            with self.assertRaisesRegex(ValueError,'OWNED_ROOT'):p.root_access(a,b,out,time.monotonic()+10,str(link))
            b['root']=str(base/'codex03-tm-probe');(base/'siw-lab.lock').unlink();(base/'target').touch();(base/'siw-lab.lock').symlink_to(base/'target')
            with self.assertRaisesRegex(ValueError,'OWNED_ROOT|LOCK_ALIAS'):p.root_access(a,b,out,time.monotonic()+10,str(link))
    def test_real_fsync_failure_cleanup_keeps_primary(self):
        with tempfile.TemporaryDirectory() as directory:
            base=pathlib.Path(directory).resolve();out=base/'out';out.mkdir();b=dict(root=str(base/'codex03-tm-probe'),lock_path=str(base/'siw-lab.lock'))
            with patch.object(os,'fsync',side_effect=PermissionError('ACTUAL_FSYNC_BOUNDARY')),self.assertRaisesRegex(PermissionError,'ACTUAL_FSYNC_BOUNDARY'):p.root_access(a,b,out,time.monotonic()+10,str(base))
            row=json.loads((out/'run-root-access.json').read_text());self.assertTrue(row['cleanup']['scratch_absent']);self.assertIn('ACTUAL_FSYNC_BOUNDARY',row['primary_refusal'])
    def test_real_child_alarm_reaped_original_refusal_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            out=pathlib.Path(directory);previous=signal.getsignal(signal.SIGALRM)
            def expired(signum,frame):raise ValueError('PROBE_PRODUCER_DEADLINE')
            signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,.15)
            try:
                with self.assertRaisesRegex(ValueError,'PROBE_PRODUCER_DEADLINE'):a.command([sys.executable,'-c','import os,time;print(os.getpid(),flush=True);time.sleep(60)'],out,'real-child',time.monotonic()+20)
            finally:signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)
            pid=int((out/'real-child.stdout.raw').read_text());self.assertIsNone(a.proc(pid));row=json.loads((out/'real-child.exit.json').read_text());self.assertTrue(row['cleanup']['reaped']);self.assertIn('PROBE_PRODUCER_DEADLINE',row['primary_refusal'])
    def test_command_missing_timeout_and_cancellation_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            out=pathlib.Path(directory)
            with self.assertRaises(FileNotFoundError):a.command(['/NONEXISTENT_PROBE_COMMAND'],out,'missing',time.monotonic()+1)
            self.assertIn('FileNotFoundError',json.loads((out/'missing.exit.json').read_text())['primary_refusal'])
            with self.assertRaisesRegex(ValueError,'COMMAND_REFUSED'):a.command([sys.executable,'-c','import time;time.sleep(60)'],out,'timeout',time.monotonic()+.1)
            self.assertTrue(json.loads((out/'timeout.exit.json').read_text())['cleanup']['reaped'])
            original=subprocess.Popen.wait;calls=[]
            def interrupted(child,*args,**kwargs):
                if not calls:calls.append(child.pid);raise KeyboardInterrupt()
                return original(child,*args,**kwargs)
            with patch.object(subprocess.Popen,'wait',interrupted):
                with self.assertRaises(KeyboardInterrupt):a.command([sys.executable,'-c','import time;time.sleep(60)'],out,'cancel',time.monotonic()+1)
            self.assertIsNone(a.proc(calls[0]));self.assertTrue(json.loads((out/'cancel.exit.json').read_text())['cleanup']['reaped'])
    def test_output_manifest_allows_authenticated_envelope_not_input_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            out=pathlib.Path(directory);(out/'envelope.json').write_text('{}');a.seal(out)
            with self.assertRaisesRegex(ValueError,'AUTHORITY_CYCLE'):a.manifest(out)
            self.assertEqual(a.manifest(out,output_receipt=True),{'envelope.json'})
    def test_plans_are_help_derived_not_service_successes(self):
        with tempfile.TemporaryDirectory() as directory:
            build=pathlib.Path(directory);(build/'server-help.stdout.raw').write_text('\n'.join(['--'+x for x in ['port','port.grpc','ip','ip.bind','master','dir','max','rdma.enabled','rdma.ip','rdma.port']]));(build/'server-help.stderr.raw').write_text('');(build/'master-help.stdout.raw').write_text('master')
            row=p.plans(a,build,{},dict(ip='198.51.100.1'));self.assertEqual(row['state'],'ARTIFACT_HELP_DERIVED_PLANS_NOT_EXECUTED');self.assertIn('NOT_RUN',row['runtime_renderer'])
            (build/'server-help.stdout.raw').write_text('--port')
            with self.assertRaisesRegex(ValueError,'PLAN_UNSUPPORTED'):p.plans(a,build,{},dict(ip='198.51.100.1'))
    def test_producer_chain_retains_facts_and_typed_refusal(self):
        # Host command boundaries are controlled locally; this executes the real
        # producer and actual UID filesystem admission, and certifies no siw.
        for case in ('positive','module-missing','runner-missing','tool-missing'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as directory:
                base=pathlib.Path(directory).resolve();out=base/'out';out.mkdir();build=out/'build';build.mkdir()
                (build/'server-help.stdout.raw').write_text('\n'.join('--'+x for x in ['port','port.grpc','ip','ip.bind','master','dir','max','rdma.enabled','rdma.ip','rdma.port']))
                (build/'server-help.stderr.raw').write_text('');(build/'master-help.stdout.raw').write_text('master')
                libs=base/'lib';provider=libs/'libibverbs';provider.mkdir(parents=True)
                for file in (libs/'libibverbs.so.1',libs/'librdmacm.so.1',provider/'libsiw-rdmav1.so'):file.write_bytes(b'local boundary fixture')
                env=dict(GITHUB_SHA='1'*40,GITHUB_RUN_ID='10',GITHUB_RUN_ATTEMPT='1',GITHUB_JOB='tm-connected-diagnostic',RUNNER_NAME='seat1')
                b=dict(root=str(base/'codex03-tm-probe'),lock_path=str(base/'siw-lab.lock'))
                def command(argv,dest,label,deadline):
                    if case=='tool-missing' and label=='probe-kernel':raise FileNotFoundError('uname')
                    responses={'probe-kernel':'local-kernel','probe-siw-module':'srcversion: local-module\nvermagic: local-kernel',
                               'probe-rdma-link':'fixture','probe-IP':'[]','probe-memlock':'unlimited','probe-devices':'siw0 fixture-gid',
                               'probe-ldconfig':f'libibverbs.so.1 (libc6) => {libs}/libibverbs.so.1\nlibrdmacm.so.1 (libc6) => {libs}/librdmacm.so.1',
                               'probe-master-flags':'-ip value\n-port value\n-port.grpc value\n-mdir value'}
                    (dest/(label+'.stderr.raw')).write_text('');return responses[label].encode()
                def jobs(path,deadline):
                    if path.endswith('runs?per_page=10'):return dict(workflow_runs=[dict(id=1)])
                    return dict(total_count=2,jobs=[dict(runner_name=name,labels=['tp01']) for name in (['foreign','seat2'] if case=='runner-missing' else ['seat1','seat2'])])
                read=pathlib.Path.read_text;access=p.root_access
                def read_text(file,*args,**kwargs):
                    if str(file)=='/proc/modules':return '' if case=='module-missing' else 'siw 100 0 - Live 0\n'
                    return read(file,*args,**kwargs)
                with patch.object(a,'command',command),patch.object(pathlib.Path,'read_text',read_text),patch.object(a,'ports_free'),patch.object(p,'network',return_value=dict(gid='fixture-gid',gid_index=0,netdev='fixture-net',ip='198.51.100.1')),patch.object(p,'root_access',side_effect=lambda aa,bb,oo,dd:access(aa,bb,oo,dd,str(base))):
                    if case=='positive':self.assertIn('PLANS_NOT_EXECUTED',p.produce(a,b,out,env,types.SimpleNamespace(repository='fixture/repo',get=jobs)))
                    else:
                        with self.assertRaises((ValueError,FileNotFoundError)):p.produce(a,b,out,env,types.SimpleNamespace(repository='fixture/repo',get=jobs))
                row=json.loads((out/'probe.json').read_text());self.assertLess(row['producer_seconds'],20);self.assertFalse(pathlib.Path(b['root']).exists())
                self.assertEqual(row['state'],'PASS_FACTS_PLANS_NOT_RUN' if case=='positive' else 'FAIL')
                if case=='positive':self.assertEqual(set(row['provider_objects']),{'libsiw','libibverbs','librdmacm'});self.assertEqual(row['uid'],os.getuid())
                if case=='runner-missing':self.assertTrue((out/'runner-inventory.json').exists())
                if case=='tool-missing':self.assertEqual(row['error_code'],'PROBE_FileNotFoundError')
    def test_run_consumer_refuses_foreign_runner_hash_fact_and_lock_drift(self):
        for case in ('positive','runner','hash','fact','lock','plan'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as directory:
                out=pathlib.Path(directory);lock=out/'siw-lab.lock';lock.touch();st=lock.stat()
                row=dict(state='PASS_FACTS_PLANS_NOT_RUN',ci_sha='1'*40,source_sha=p.MONO_SHA,build_ci_sha=p.BUILD_SHA,run_id=10,attempt=1,job='tm-connected-diagnostic',runner='seat1',hostname=p.socket.gethostname(),uid=os.getuid(),kernel_release='fixture',siw_module_required_lines=[],gid='fixture',gid_index=0,netdev='fixture',ip='198.51.100.1',provider_objects={},runner_names=['seat1','seat2'],runtime_access=dict(base=str(out),lock=str(lock),lock_identity=dict(dev=st.st_dev,inode=st.st_ino,uid=st.st_uid)))
                b=dict(row,ci_sha='1'*40,root=str(out/'codex03-tm-run'),lock_path=str(lock),probe_reference={},plans_status='REVIEWED_RENDERED_OWNED_WRAPPERS')
                def artifact(ref,dest,deadline):
                    dest.mkdir();a.save(dest/'probe.json',row);a.seal(dest)
                    return dict(head_sha='1'*40,id=10,run_attempt=1),None
                dest=out/'expected';dest.mkdir();a.save(dest/'probe.json',row);digest=a.sha(dest/'probe.json');b.update(probe_json_sha256=digest,probe_plan_source_sha256=digest)
                env=dict(RUNNER_NAME='foreign' if case=='runner' else 'seat1')
                if case=='hash':b['probe_json_sha256']='0'*64
                if case=='fact':b['gid_index']=1
                if case=='lock':row['runtime_access']['lock_identity']['inode']+=1;a.save(dest/'probe.json',row);b['probe_json_sha256']=a.sha(dest/'probe.json')
                if case=='plan':b['plans_status']='UNREVIEWED'
                api=types.SimpleNamespace(artifact=artifact,repository='fixture/repo',get=lambda path,deadline:dict(total_count=1,jobs=[dict(name='tm-connected-diagnostic',runner_name='seat1',conclusion='success')]))
                if case=='positive':p.run_admit(a,b,out,env,api,time.monotonic()+10)
                else:
                    with self.assertRaises(ValueError):p.run_admit(a,b,out,env,api,time.monotonic()+10)

if __name__=='__main__':unittest.main()

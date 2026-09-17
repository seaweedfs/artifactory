"""Execute the CI shell entrypoint with controlled subprocess receipts."""
import os,pathlib,subprocess,tempfile
ROOT=pathlib.Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as temporary:
    root=pathlib.Path(temporary); tools=root/'bin';tools.mkdir()
    product=root/'product';product.mkdir()
    pin='a'*40
    tests=product/'enterprise/rust/tests';tests.mkdir(parents=True)
    names=('s4_error_classification_contract.py','lease_explicit_finish_contract.py')
    for name in names:(tests/name).write_text('')
    (tools/'git').write_text('#!/bin/sh\nprintf "%s\\n" "$ACTUAL_PIN"\n')
    (tools/'python3').write_text('''#!/bin/sh
case "$1" in
 *s4_error*) echo 'S4-ERROR-CLASSIFICATION-SELF-TEST PASS';;
 *lease_explicit*) echo 'LEF-SELF-TEST PASS';;
 *) exit 99;;
esac
[ "$MODE" != missing_receipt ] || exit 0
[ "$MODE" != nonzero ] || exit 7
''')
    for tool in tools.iterdir():tool.chmod(0o755)
    env=dict(os.environ,PATH=str(tools)+':'+os.environ['PATH'],ACTUAL_PIN=pin)
    def run(mode,expected,pin_arg=pin,actual=pin):
        env.update(MODE=mode,ACTUAL_PIN=actual)
        # Missing-receipt control deletes output in the real subprocess seam.
        if mode=='missing_receipt':
            (tools/'python3').write_text('#!/bin/sh\nexit 0\n')
            (tools/'python3').chmod(0o755)
        r=subprocess.run(['bash',str(ROOT/'rdma-lab-ci/contract-self-tests.sh'),str(product),pin_arg],env=env,capture_output=True,text=True,timeout=10)
        print('F3-CONTROL',mode,'rc='+str(r.returncode),flush=True)
        assert (r.returncode==0)==expected,r.stdout+r.stderr
        if mode in ('nonzero','missing_receipt'):
            assert r.stdout.count('CONTRACT-VERDICT')==2
            assert 'failed=1' in r.stdout
    run('positive',True)
    run('nonzero',False)
    run('mismatch',False,actual='b'*40)
    run('detached',False,pin_arg='HEAD')
    run('missing_receipt',False)
    # One contract missing = broken RDMA tree: still fails closed.
    (tests/names[1]).unlink()
    run('one_missing',False)
    # Neither contract in the product (non-RDMA branch): not applicable, pass.
    (tests/names[0]).unlink()
    r=subprocess.run(['bash',str(ROOT/'rdma-lab-ci/contract-self-tests.sh'),str(product),pin],env=env,capture_output=True,text=True,timeout=10)
    print('F3-CONTROL not_applicable rc='+str(r.returncode),flush=True)
    assert r.returncode==0 and 'status=NOT_APPLICABLE' in r.stdout and 'CONTRACT-VERDICT' not in r.stdout,r.stdout+r.stderr
print('F3-SELF-TEST PASS controls=7')

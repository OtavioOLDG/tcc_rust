"""Verificação offline: todas as operações Docker/processo/rede são simuladas."""
import copy
import hashlib
import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'cold_start_docker_start.py'
spec=importlib.util.spec_from_file_location('docker_start_harness',SOURCE)
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
CID='a'*64

class Daemon:
    def __init__(self,context):
        self.context=context; self.object=None; self.calls=[]; self.start_calls=0
        self.fail_stop=False; self.fail_create=False;self.start_code=0
    def command(self,context,args,**kwargs):
        self.calls.append(list(args))
        if args[:2]==['container','create']:
            self.object={'Id':CID,'Name':'/'+context['container_name'],'Image':h.DOCKER_IMAGE_ID,
                'Config':{'Labels':{h.OWNER_LABEL:context['campaign_id'],h.EXPERIMENT_LABEL:h.EXPERIMENT}},
                'State':{'Status':'created','Running':False,'Pid':0,'Paused':False,'Restarting':False,
                    'StartedAt':'0001-01-01T00:00:00Z','FinishedAt':'0001-01-01T00:00:00Z','ExitCode':0}}
            Path(args[args.index('--cidfile')+1]).write_text(CID+'\n')
            if self.fail_create:raise h.ProceduralError('create timeout after daemon creation')
            return 0,CID,''
        if args[:2]==['container','ls']:
            return 0,CID if self.object else '', ''
        if args[:2] in (['container','stop'],['container','kill']):
            if self.fail_stop and args[1]=='stop':raise h.ProceduralError('stop timeout')
            assert self.object['Id']==args[-1]
            self.object['State'].update(Status='exited',Running=False,Pid=0,ExitCode=0,FinishedAt='finished')
            return 0,CID,''
        if args[:2]==['container','rm']:
            assert self.object['Id']==args[-1];assert not self.object['State']['Running']
            self.object=None;return 0,CID,''
        raise AssertionError('Comando Docker inesperado: '+repr(args))
    def inspect(self,context,name,**kwargs):
        if self.object is None:return None
        if name not in [CID,context['container_name']]:return None
        return copy.deepcopy(self.object)
    def start(self,args,**kw):
        self.calls.append(list(args));assert args==self.context['docker_base']+['start',CID]
        assert self.object['State']['Status'] in ['created','exited']
        self.start_calls+=1
        self.object['State'].update(Status='running',Running=True,Pid=200,StartedAt=str(self.start_calls))
        kw['stdout'].write((CID+'\n').encode());kw['stdout'].flush()
        return type('FakeProcess',(),{'pid':9876})()

class HarnessTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.context={'config':dict(h.CONFIG),'env':{},'source_hash':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            'campaign_id':'b'*32,'root':self.root/'campaign','container_name':h.CONTAINER_PREFIX+'b'*32,
            'container_id':None,'create_attempted':False,'start_attempts':0,'preparation':{},'provenance':{},
            'temporary_dir':None,'docker_base':['sudo','-n','--','docker','--host',h.DOCKER_SOCKET]}
        self.daemon=Daemon(self.context);self.campaign={'campaign_id':'b'*32,'mode':'official'};self.records=[]
        self.patchers=[patch.object(h,'docker_command',self.daemon.command),patch.object(h,'inspect_container',self.daemon.inspect),
            patch.object(h,'port_is_open',return_value=False),patch.object(h.socket,'socket'),
            patch.object(h,'EXPECTED_SCRIPT_PATH',SOURCE),patch.object(h.subprocess,'Popen',side_effect=self.daemon.start),
            patch.object(h,'child_exit_code',side_effect=lambda p:self.daemon.start_code),
            patch.object(h,'wait_child',side_effect=lambda *a:self.daemon.start_code),
            patch.object(h,'stop_owned_process',side_effect=lambda p:{'forced':False,'returncode':self.daemon.start_code}),
            patch.object(h,'probe_once',side_effect=self.probe),patch.object(h,'STOP_SIGNAL',None)]
        for p in self.patchers:p.start()
    def tearDown(self):
        for p in reversed(self.patchers):p.stop()
        self.tmp.cleanup()
    def probe(self,url,deadline):
        start=h.time.monotonic_ns();end=h.time.monotonic_ns()
        return {'start_ns':start,'end_ns':end,'duration_ns':end-start,'deadline_ns':deadline,
            'timeout_ns':deadline-start,'deadline_exceeded':False,'status':200,'content_type':'application/json',
            'body_hex':h.EXPECTED_BODY.hex(),'body':h.EXPECTED_BODY.decode(),'error':None,'valid':True}
    def create(self):h.create_container(self.context)
    def run_sample(self,phase='official',rep=1):
        return h.run_one(self.context,phase,rep,self.campaign,self.records)
    def conditioning(self):return self.run_sample('conditioning',None)
    def test_full_campaign_one_create_31_starts_32_json(self):
        self.create();c=self.conditioning();self.assertEqual(c['container_state_before_start']['Status'],'created')
        for i in range(1,31):
            r=self.run_sample(rep=i)
            self.assertTrue(r['sample_valid']);self.assertTrue(r['success'])
            self.assertEqual(r['prior_start_attempts'],i)
            self.assertEqual(r['container_state_before_start']['Status'],'exited')
            self.assertEqual(r['container_id'],CID)
            self.assertGreaterEqual(r['monotonic_start_ns'],r['container_preparation']['monotonic_end_ns'])
            self.assertEqual(r['cold_start_ns'],r['monotonic_ready_ns']-r['monotonic_start_ns'])
            self.assertFalse(r['cleanup']['container_removal_confirmed'])
        cleanup=h.cleanup_campaign(self.context);self.assertTrue(cleanup['complete']);self.assertTrue(cleanup['container_removal_confirmed'])
        self.assertEqual(sum(x[:2]==['container','create'] for x in self.daemon.calls),1)
        self.assertEqual(self.daemon.start_calls,31)
        self.assertEqual(sum(x[:2]==['container','rm'] for x in self.daemon.calls),1)
        self.assertEqual(len(self.records),31)
        for rec in self.records:
            payload=Path(rec['path']).read_bytes();self.assertEqual(hashlib.sha256(payload).hexdigest(),rec['sha256'])
            self.assertTrue(json.loads(payload)['sample_valid']);self.assertTrue(rec['publication_confirmed'])
        manifest=self.context['root']/'manifest.json';h.write_json_exclusive(manifest,{'files':self.records})
        self.assertEqual(len(list(self.context['root'].rglob('*.json'))),32)
    def test_timeout_valid_preserved_followed_by_next_attempt(self):
        self.create();self.conditioning()
        with patch.object(h,'STARTUP_TIMEOUT_SECONDS',0.0):r=self.run_sample()
        self.assertFalse(r['success']);self.assertTrue(r['sample_valid']);self.assertEqual(r['failure_reason'],'startup_timeout')
        self.assertIsNone(r['cold_start_ns']);self.assertIsNone(r['cold_start_ms'])
        r2=self.run_sample(rep=2);self.assertTrue(r2['sample_valid']);self.assertEqual(r2['container_id'],CID)
    def test_failed_start_procedural_discard(self):
        self.create();self.conditioning();self.daemon.start_code=1
        with self.assertRaises(h.ProceduralError):self.run_sample()
        record=self.records[-1];self.assertFalse(record['sample_valid']);self.assertIn('/discarded/',record['path'])
        self.assertTrue(json.loads(Path(record['path']).read_text())['cleanup']['container_stopped'])
    def test_stop_timeout_cleanup_error_remains_invalid_even_after_kill(self):
        self.create();self.conditioning();self.daemon.fail_stop=True
        with self.assertRaises(h.ProceduralError):self.run_sample()
        r=json.loads(Path(self.records[-1]['path']).read_text());self.assertTrue(r['success']);self.assertFalse(r['sample_valid'])
        self.assertTrue(r['cleanup']['forced']);self.assertIn('stop timeout',r['procedural_error'])
    def test_popen_error_still_cleans_existing_container(self):
        self.create();self.conditioning()
        with patch.object(h.subprocess,'Popen',side_effect=OSError('cannot start CLI')):
            with self.assertRaises(h.ProceduralError):self.run_sample()
        r=json.loads(Path(self.records[-1]['path']).read_text());self.assertTrue(r['cleanup']['container_stopped'])
        final=h.cleanup_campaign(self.context);self.assertTrue(final['container_removal_confirmed'])
    def test_creation_timeout_reconciles_removes_only_own_id(self):
        self.daemon.fail_create=True
        with self.assertRaises(h.ProceduralError):self.create()
        cleanup=h.cleanup_campaign(self.context);self.assertTrue(cleanup['container_removal_confirmed'])
        self.assertEqual(self.daemon.start_calls,0)
    def test_foreign_container_never_stopped_removed(self):
        self.create();self.daemon.object['Config']['Labels'][h.OWNER_LABEL]='foreign'
        cleanup=h.cleanup_campaign(self.context);self.assertFalse(cleanup['complete'])
        self.assertFalse(any(x[:2] in [['container','stop'],['container','kill'],['container','rm']] for x in self.daemon.calls))
    def test_replacement_id_never_stopped(self):
        self.create();self.daemon.object['Id']='c'*64
        cleanup=h.cleanup_campaign(self.context);self.assertFalse(cleanup['complete'])
        self.assertFalse(any(x[:2]==['container','rm'] for x in self.daemon.calls))
    def test_signal_preserves_discard(self):
        self.create();self.conditioning()
        with patch.object(h,'STOP_SIGNAL',2):
            with self.assertRaises(h.ProceduralError):self.run_sample()
        r=json.loads(Path(self.records[-1]['path']).read_text());self.assertFalse(r['sample_valid']);self.assertIn('sinal 2',r['procedural_error'])
    def test_exclusive_publication_and_finite_numbers(self):
        path=self.root/'exclusive.json';h.write_json_exclusive(path,{'ok':1});before=path.read_bytes()
        with self.assertRaises(h.PublicationError):h.write_json_exclusive(path,{'ok':2})
        self.assertEqual(before,path.read_bytes())
        with self.assertRaises(ValueError):h.write_json_exclusive(self.root/'nan.json',{'n':math.nan})
        self.assertFalse((self.root/'nan.json').exists())
    def test_old_results_paths_not_used(self):
        self.create();self.conditioning();self.run_sample()
        for r in self.records:self.assertTrue(Path(r['path']).is_relative_to(self.context['root']))
        self.assertFalse(any('/results/official/docker/' in r['path'] for r in self.records))
    def test_run_cannot_skip_conditioning(self):
        self.create()
        with self.assertRaises(h.ProceduralError):self.run_sample()
        self.assertFalse(self.records[-1]['sample_valid'])
    def test_change_of_harness_discarded_before_start(self):
        self.create();self.conditioning();n=self.daemon.start_calls
        with patch.object(h,'sha256_file',return_value='0'*64):
            with self.assertRaises(h.ProceduralError):self.run_sample()
        self.assertEqual(self.daemon.start_calls,n);self.assertFalse(self.records[-1]['sample_valid'])
    def test_stop_cli_has_separate_15s_budget_and_5s_grace(self):
        self.create();self.conditioning()
        original=self.daemon.command
        seen=[]
        def command(context,args,**kw):
            if args[:2]==['container','stop']:
                seen.append((args,kw));kw['observation'].update(outcome='success',wait_status='exited',cli_returncode=0)
            return original(context,args,**kw)
        with patch.object(h,'docker_command',side_effect=command):r=self.run_sample()
        self.assertTrue(r['sample_valid']);args,kw=seen[0]
        self.assertEqual(args[args.index('--time')+1],'5');self.assertEqual(kw['timeout'],15.0)
        self.assertFalse(kw['interruptible']);self.assertEqual(r['cleanup']['container_status_after_stop'],'exited')
        self.assertEqual(r['cleanup']['docker_stop_command']['outcome'],'success')
    def test_cli_timeout_with_confirmed_stop_still_discards(self):
        self.create();self.conditioning();original=self.daemon.command
        def command(context,args,**kw):
            if args[:2]==['container','stop']:
                original(context,args,**kw)
                kw['observation'].update(outcome='timeout',wait_status='timeout',timeout_seconds=15.0)
                raise h.ProceduralError('Comando Docker excedeu 15.0s: container stop.')
            return original(context,args,**kw)
        with patch.object(h,'docker_command',side_effect=command):
            with self.assertRaises(h.ProceduralError):self.run_sample()
        r=json.loads(Path(self.records[-1]['path']).read_text())
        self.assertTrue(r['success']);self.assertFalse(r['sample_valid']);self.assertFalse(r['cleanup']['complete'])
        self.assertTrue(r['cleanup']['container_stopped']);self.assertTrue(r['cleanup']['port_closed'])
        self.assertFalse(r['cleanup']['forced']);self.assertEqual(r['cleanup']['stop_outcome'],'cli_timeout_container_stopped')
    def test_exited_137_without_explicit_kill_remains_valid(self):
        self.create();self.conditioning();original=self.daemon.command
        def command(context,args,**kw):
            response=original(context,args,**kw)
            if args[:2]==['container','stop']:
                self.daemon.object['State'].update(ExitCode=137,OOMKilled=False)
            return response
        with patch.object(h,'docker_command',side_effect=command):r=self.run_sample()
        self.assertTrue(r['sample_valid']);self.assertFalse(r['cleanup']['forced'])
        self.assertTrue(r['cleanup']['forced_or_exit_137']);self.assertFalse(r['cleanup']['container_oom_killed_after_stop'])
    def test_dead_pid_zero_cannot_be_declared_reusable(self):
        self.create();self.conditioning();original=self.daemon.command
        def command(context,args,**kw):
            response=original(context,args,**kw)
            if args[:2]==['container','stop']:self.daemon.object['State'].update(Status='dead',Dead=True)
            return response
        with patch.object(h,'docker_command',side_effect=command):
            with self.assertRaises(h.ProceduralError):self.run_sample()
        r=json.loads(Path(self.records[-1]['path']).read_text());self.assertFalse(r['sample_valid'])
        self.assertFalse(r['cleanup']['container_stopped']);self.assertFalse(r['cleanup']['container_preserved_for_reuse'])
    def test_failed_first_start_created_container_can_still_be_removed(self):
        self.create()
        with patch.object(h.subprocess,'Popen',side_effect=OSError('cannot start first CLI')):
            with self.assertRaises(h.ProceduralError):self.conditioning()
        self.assertEqual(self.daemon.object['State']['Status'],'created')
        final=h.cleanup_campaign(self.context);self.assertTrue(final['container_removal_confirmed'])
    def test_state_confirmation_retries_then_observes_exited(self):
        self.create();self.conditioning()
        snapshot=copy.deepcopy(self.daemon.object)
        running=copy.deepcopy(snapshot);running['State'].update(Status='running',Running=True,Pid=123)
        response=iter([running,snapshot]);cleanup={'forced':False}
        with patch.object(h,'inspect_container',side_effect=lambda *a,**kw:next(response)),patch.object(h.time,'sleep'):
            self.assertTrue(h.confirm_container_stopped(self.context,CID,cleanup,'after_stop',True))
        self.assertEqual(len(cleanup['state_confirmation_checks']),2)
        self.assertEqual(cleanup['container_status_after_stop'],'exited')
    def test_identity_changed_during_state_confirmation_never_kills_replacement(self):
        self.create();self.conditioning();original=self.daemon.command
        def command(context,args,**kw):
            response=original(context,args,**kw)
            if args[:2]==['container','stop']:self.daemon.object['Config']['Labels'][h.OWNER_LABEL]='foreign'
            return response
        with patch.object(h,'docker_command',side_effect=command):
            with self.assertRaises(h.ProceduralError):self.run_sample()
        self.assertFalse(any(x[:2]==['container','kill'] for x in self.daemon.calls))
        self.assertFalse(self.records[-1]['sample_valid'])
    def test_v1_measurement_parameters_and_probe_function_unchanged(self):
        import ast
        old_path=SOURCE.parent/'harness-versions/docker-start/versao-anterior-v1'/SOURCE.name
        self.assertEqual(hashlib.sha256(old_path.read_bytes()).hexdigest(),h.PARENT_HARNESS_SHA256)
        old_tree=ast.parse(old_path.read_text());new_tree=ast.parse(SOURCE.read_text())
        names=['OFFICIAL_REPETITIONS','PILOT_REPETITIONS','STABILIZATION_SECONDS','STARTUP_TIMEOUT_SECONDS',
            'PROBE_TIMEOUT_SECONDS','PROBE_INTERVAL_SECONDS','COOLDOWN_SECONDS','EXPECTED_BODY','DOCKER_IMAGE_ID',
            'EXPERIMENT','MEASUREMENT_DEFINITION','CONFIG']
        def assignments(tree):
            return {n.targets[0].id:ast.dump(n.value) for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name)}
        a=assignments(old_tree);b=assignments(new_tree)
        for name in names:self.assertEqual(a[name],b[name],name)
        old_probe=next(n for n in old_tree.body if isinstance(n,ast.FunctionDef) and n.name=='probe_once')
        new_probe=next(n for n in new_tree.body if isinstance(n,ast.FunctionDef) and n.name=='probe_once')
        self.assertEqual(ast.dump(old_probe),ast.dump(new_probe))
        self.assertEqual(h.SCHEMA_VERSION,4);self.assertEqual(h.HARNESS_VERSION,'docker-start-reused-v2')
        self.assertNotEqual(self.context['source_hash'],h.PARENT_HARNESS_SHA256)
    def test_confirmation_has_bounded_wait_and_inspect_timeout(self):
        self.create();self.conditioning();state=copy.deepcopy(self.daemon.object)
        state['State'].update(Status='running',Running=True,Pid=123)
        times=iter([100.0,100.1,100.5,103.1]);calls=[]
        def inspect(*a,**kw):calls.append(kw);return state
        with patch.object(h.time,'monotonic',side_effect=lambda:next(times)),patch.object(h.time,'sleep'),patch.object(h,'inspect_container',side_effect=inspect):
            self.assertFalse(h.confirm_container_stopped(self.context,CID,{},'after_stop',True))
        self.assertEqual(len(calls),1);self.assertLessEqual(calls[0]['timeout'],3.0)

    def execute_main(self,wrapper=None):
        import contextlib,io
        digest=self.context['source_hash']
        def context_builder(campaign,source_hash):
            self.context['campaign_id']=campaign['campaign_id']
            self.context['container_name']=h.CONTAINER_PREFIX+campaign['campaign_id']
            return self.context
        patches=[patch.object(h,'verify_static_environment',return_value=digest),
            patch.object(h,'acquire_lock',return_value=None),patch.object(h,'prepare_context',side_effect=context_builder),
            patch.object(h,'verify_docker'),patch.object(h,'host_metadata',return_value={'test':'offline'}),
            patch.object(h,'sleep_interruptible'),patch.object(h.signal,'getitimer',return_value=(0.0,0.0)),
            patch.object(h.signal,'signal'),patch.object(h.signal,'setitimer'),
            patch.object(h.sys,'argv',['cold_start_docker_start.py','official'])]
        if wrapper:patches.append(patch.object(h,'run_one',side_effect=wrapper))
        with contextlib.ExitStack() as stack:
            for item in patches:stack.enter_context(item)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            code=h.main()
        return code,json.loads((self.context['root']/'manifest.json').read_text())
    def test_main_complete_with_three_real_timeouts(self):
        original=h.run_one
        def wrapper(context,phase,rep,campaign,records):
            if rep in [7,20,30]:
                with patch.object(h,'STARTUP_TIMEOUT_SECONDS',0.0):return original(context,phase,rep,campaign,records)
            return original(context,phase,rep,campaign,records)
        code,m=self.execute_main(wrapper)
        self.assertEqual(code,2);self.assertTrue(m['complete']);self.assertIsNone(m['error'])
        self.assertEqual(m['real_failures'],3);self.assertEqual(m['completed_repetitions'],30)
        self.assertEqual(m['start_attempts'],31);self.assertEqual(len(m['files']),31)
        self.assertEqual([r['repetition'] for r in m['files'] if r.get('success') is False],[7,20,30])
        self.assertTrue(m['cleanup']['container_removal_confirmed'])
    def test_r13_cli_timeout_keeps_12_valid_one_failure_and_final_cleanup(self):
        original_run=h.run_one;original_command=self.daemon.command
        def wrapper(context,phase,rep,campaign,records):
            if rep==1:
                with patch.object(h,'STARTUP_TIMEOUT_SECONDS',0.0):return original_run(context,phase,rep,campaign,records)
            if rep==13:
                def timeout(context,args,**kw):
                    response=original_command(context,args,**kw)
                    if args[:2]==['container','stop']:
                        kw['observation'].update(outcome='timeout',wait_status='timeout')
                        raise h.ProceduralError('CLI stop timeout after container stopped')
                    return response
                with patch.object(h,'docker_command',side_effect=timeout):return original_run(context,phase,rep,campaign,records)
            return original_run(context,phase,rep,campaign,records)
        code,m=self.execute_main(wrapper)
        self.assertEqual(code,1);self.assertFalse(m['complete']);self.assertEqual(m['completed_repetitions'],12)
        self.assertEqual(m['real_failures'],1);self.assertTrue(m['cleanup']['complete'])
        self.assertTrue(m['cleanup']['container_removal_confirmed'])
        r=json.loads(Path(m['files'][-1]['path']).read_text())
        self.assertTrue(r['success']);self.assertFalse(r['sample_valid']);self.assertFalse(r['cleanup']['complete'])
        self.assertTrue(r['cleanup']['container_stopped']);self.assertTrue(r['cleanup']['port_closed'])

    def test_main_signal_preserves_partial_manifest(self):
        original=h.run_one
        def wrapper(context,phase,rep,campaign,records):
            if rep==4:h.request_stop(2,None)
            return original(context,phase,rep,campaign,records)
        code,m=self.execute_main(wrapper)
        self.assertEqual(code,1);self.assertFalse(m['complete']);self.assertEqual(m['stop_signal'],2)
        self.assertEqual(m['completed_repetitions'],3);self.assertEqual(m['real_failures'],0)
        self.assertEqual(len(m['files']),5);self.assertFalse(m['files'][-1]['sample_valid'])
        self.assertTrue(m['cleanup']['container_removal_confirmed'])
    def test_main_conditioning_timeout_stops_without_official_attempts(self):
        original=h.run_one
        def wrapper(context,phase,rep,campaign,records):
            with patch.object(h,'STARTUP_TIMEOUT_SECONDS',0.0):return original(context,phase,rep,campaign,records)
        code,m=self.execute_main(wrapper)
        self.assertEqual(code,1);self.assertFalse(m['complete']);self.assertEqual(m['completed_repetitions'],0)
        self.assertEqual(m['real_failures'],0);self.assertEqual(m['start_attempts'],1)
        self.assertEqual(len(m['files']),1);self.assertTrue(m['files'][0]['sample_valid'])
    def test_main_final_cleanup_failure_prevents_completion(self):
        original=self.daemon.command
        def command(context,args,**kw):
            if args[:2]==['container','rm']:return 1,'','removal error'
            return original(context,args,**kw)
        with patch.object(h,'docker_command',side_effect=command):code,m=self.execute_main()
        self.assertEqual(code,1);self.assertFalse(m['complete']);self.assertEqual(m['completed_repetitions'],30)
        self.assertFalse(m['cleanup']['complete']);self.assertIn('Cleanup final:',m['error'])
    def test_main_publication_failure_keeps_partial_and_snapshot(self):
        original=h.write_json_exclusive
        def writer(path,data):
            if path.name=='r02.json':raise h.PublicationError(path,'0'*64,False,'test')
            return original(path,data)
        with patch.object(h,'write_json_exclusive',side_effect=writer):code,m=self.execute_main()
        self.assertEqual(code,1);self.assertFalse(m['complete']);self.assertEqual(m['completed_repetitions'],1)
        self.assertFalse(m['files'][-1]['publication_confirmed']);self.assertIn('result_snapshot',m['files'][-1])

    def test_wrong_vm_rejected_before_any_docker(self):
        with patch.object(h.os,'geteuid',return_value=1000),patch.object(h.socket,'gethostname',return_value='benchmark-local'):
            with self.assertRaises(h.ProceduralError):h.verify_static_environment()
        self.assertEqual(self.daemon.calls,[])

class CommandObservationTest(unittest.TestCase):
    def call_command(self,code,cleanup_error=None,wait_error=None):
        import contextlib
        obs={};context={'docker_base':['docker'],'env':{}}
        def spawn(command,**kw):
            kw['stdout'].write(b'container-id\n');kw['stderr'].write(b'diagnostic\n')
            return type('FakeProcess',(),{'pid':8765})()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(h.subprocess,'Popen',side_effect=spawn))
            stack.enter_context(patch.object(h,'wait_child',side_effect=wait_error,return_value=code))
            stack.enter_context(patch.object(h,'stop_owned_process',side_effect=cleanup_error,
                return_value={'forced':code is None,'returncode':-15 if code is None else code}))
            try:r=h.docker_command(context,['container','stop','--time','5',CID],timeout=15.0,observation=obs)
            except BaseException as exc:r=exc
        return r,obs
    def test_timeout_retains_diagnostics_and_auxiliary_cleanup(self):
        error,obs=self.call_command(None)
        self.assertIsInstance(error,h.ProceduralError);self.assertIn('container stop',str(error));self.assertIn('15.0s',str(error))
        self.assertEqual(obs['outcome'],'timeout');self.assertEqual(obs['wait_status'],'timeout')
        self.assertTrue(obs['process_cleanup_complete']);self.assertTrue(obs['cli_forced'])
        self.assertEqual(obs['stderr_tail'],'diagnostic');self.assertEqual(obs['stdout_tail'],'container-id')
        self.assertGreaterEqual(obs['duration_including_cli_cleanup_ms'],obs['wait_duration_ms'])
    def test_success_has_confirmed_zero_returncode(self):
        result,obs=self.call_command(0);self.assertEqual(result,(0,'container-id','diagnostic'))
        self.assertEqual(obs['outcome'],'success');self.assertFalse(obs['cli_forced']);self.assertEqual(obs['cli_returncode'],0)
    def test_nonzero_exit_is_not_timeout(self):
        result,obs=self.call_command(1);self.assertEqual(result[0],1)
        self.assertEqual(obs['outcome'],'nonzero_exit');self.assertEqual(obs['wait_status'],'exited')
    def test_cli_cleanup_failure_cannot_be_silently_accepted(self):
        error,obs=self.call_command(0,cleanup_error=h.ProceduralError('session unknown'))
        self.assertIsInstance(error,h.ProceduralError);self.assertFalse(obs['process_cleanup_complete'])
        self.assertIn('session unknown',obs['process_cleanup_error'])
    def test_wait_error_keeps_attempt_to_clean_cli(self):
        error,obs=self.call_command(None,wait_error=h.ProceduralError('signal 2'))
        self.assertIsInstance(error,h.ProceduralError);self.assertEqual(obs['outcome'],'wait_error')
        self.assertTrue(obs['process_cleanup_complete'])
    def test_auxiliary_wait_is_independent_of_container_grace(self):
        from unittest.mock import Mock
        process=Mock(pid=8765);process.wait.return_value=0
        with patch.object(h,'signal_owned_session'),patch.object(h,'session_members',return_value=[]),patch.object(h,'CONTAINER_STOP_GRACE_SECONDS',99.0):
            result=h.stop_owned_process(process)
        process.wait.assert_called_once_with(timeout=5.0)
        self.assertEqual(result,{'forced':False,'returncode':0})

    def test_no_duplicate_literal_dictionary_keys(self):
        import ast
        for n in ast.walk(ast.parse(SOURCE.read_text())):
            if isinstance(n,ast.Dict):
                keys=[k.value for k in n.keys if isinstance(k,ast.Constant)]
                self.assertEqual(len(keys),len(set(keys)),getattr(n,'lineno',0))

if __name__=='__main__':unittest.main(verbosity=2)

import concurrent.futures
import http.server
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from orchestrator import CommandCodeAgent,Engine,GRAPH,OpenAICompatibleCodeAgent,SecurityScanner

class WorkflowTests(unittest.TestCase):
    def setUp(self): self.temp=tempfile.TemporaryDirectory()
    def tearDown(self): self.temp.cleanup()
    def engine(self,workers=None):
        e=Engine(Path(self.temp.name)/'run.json',workers or {n:lambda n,c:{'passed':True} for n in GRAPH})
        e.start('Build short links'); return e
    def test_gate_resume_replan(self):
        e=self.engine(); e.run()
        self.assertEqual(e.state['nodes']['release']['status'],'pending')
        e.approve('human-reviewer',1)
        resumed=Engine(e.path,e.workers); resumed.run()
        self.assertEqual(resumed.state['nodes']['release']['status'],'passed')
        resumed.revise('Add seven day expiry')
        self.assertIsNone(resumed.state['approval'])
        with self.assertRaises(ValueError):resumed.approve('human-reviewer',1)
        self.assertTrue(all(n['status']=='pending' for n in resumed.state['nodes'].values()))
    def test_failure_safe_stop(self):
        workers={n:lambda n,c:{'passed':True} for n in GRAPH}
        def fail(n,c):raise RuntimeError('injected failure')
        workers['implementation']=fail
        e=self.engine(workers); e.run()
        self.assertTrue(e.state['stopped']); self.assertEqual(e.metrics()['rollback_count'],1)
        self.assertEqual(e.state['nodes']['release']['status'],'pending')
    def test_retry_recovery(self):
        workers={n:lambda n,c:{'passed':True} for n in GRAPH}; attempts=[]
        def flaky(n,c):
            attempts.append(1)
            if len(attempts)==1:raise RuntimeError('transient')
            return {'passed':True}
        workers['security']=flaky
        e=self.engine(workers);e.run()
        self.assertEqual(e.metrics()['retry_count'],1);self.assertIsNotNone(e.metrics()['mttr_seconds'])
    def test_parallel_synchronization(self):
        barrier=threading.Barrier(2);workers={n:lambda n,c:{'passed':True} for n in GRAPH}
        def parallel(n,c):barrier.wait(timeout=3);return {'passed':True}
        workers['security']=parallel;workers['documentation']=parallel
        e=self.engine(workers);e.run();self.assertEqual(e.state['nodes']['readiness']['status'],'passed')

    def test_external_agent_generates_isolated_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent_script=Path(tmp)/'agent.py'
            agent_script.write_text("import json, pathlib, sys\nrequest=json.loads(pathlib.Path(sys.argv[1]).read_text())\npathlib.Path(request['workspace'],'generated.txt').write_text('generated\\n')\n")
            root=Path(__file__).resolve().parents[1]
            agent=CommandCodeAgent([sys.executable,str(agent_script)],repository_root=root,apply_changes=False)
            artifact=agent('implementation',{'requirement':'Add generated behavior','scenario':'greenfield',
                'revision':1,'artifacts':{}})
            self.assertTrue(artifact['passed'])
            self.assertEqual(artifact['files_changed'],['generated.txt'])
            self.assertIn('+generated',artifact['patch'])
            self.assertFalse((root/'generated.txt').exists())

    def test_external_agent_applies_validated_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'repo';root.mkdir();(root/'existing.txt').write_text('before\n')
            agent_script=Path(tmp)/'agent.py'
            agent_script.write_text("import json, pathlib, sys\nrequest=json.loads(pathlib.Path(sys.argv[1]).read_text())\npathlib.Path(request['workspace'],'generated.txt').write_text('generated\\n')\n")
            agent=CommandCodeAgent([sys.executable,str(agent_script)],repository_root=root,
                validation_command=[],apply_changes=True)
            artifact=agent('implementation',{'requirement':'Add generated behavior','scenario':'greenfield',
                'revision':1,'artifacts':{}})
            self.assertEqual(artifact['applied_changes'],['generated.txt'])
            self.assertEqual((root/'generated.txt').read_text(),'generated\n')

    def test_brownfield_agent_applies_compatibility_preserving_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'repo';root.mkdir();(root/'existing.txt').write_text('before\n')
            agent_script=Path(tmp)/'agent.py'
            agent_script.write_text("import json, pathlib, sys\nrequest=json.loads(pathlib.Path(sys.argv[1]).read_text())\nassert request['scenario']=='brownfield'\npathlib.Path(request['workspace'],'existing.txt').write_text('before\\ncompatibility-preserved\\n')\n")
            agent=CommandCodeAgent([sys.executable,str(agent_script)],repository_root=root,
                validation_command=[],apply_changes=True)
            artifact=agent('implementation',{'requirement':'Improve existing behavior','scenario':'brownfield',
                'revision':1,'artifacts':{'design':{'baseline_hashes':{'existing.txt':'baseline'},
                'compatibility_contract':['preserve existing behavior']}}})
            self.assertEqual(artifact['applied_changes'],['existing.txt'])
            self.assertIn('compatibility-preserved', (root/'existing.txt').read_text())

    def test_ambiguous_requirements_block_approval_until_revision(self):
        workers={n:lambda n,c:{'passed':True} for n in GRAPH if n!='requirements'}
        e=Engine(Path(self.temp.name)/'ambiguous.json',workers=workers)
        e.start('Make analytics more useful','ambiguous');e.run()
        with self.assertRaisesRegex(ValueError,'resolved ambiguities'):
            e.approve('reviewer',1)
        e.revise('Use seven-day expiry and retain aggregate counts for 90 days')
        e.run();e.approve('reviewer',2)
        self.assertEqual(e.state['approval']['revision'],2)

    def test_agent_patch_waits_for_implementation_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'repo';root.mkdir();target=root/'existing.txt';target.write_text('before\n')
            workers={n:lambda n,c:{'passed':True} for n in GRAPH if n!='implementation_apply'}
            workers['implementation']=lambda n,c:{'passed':True,'patch':'--- a/existing.txt\n+++ b/existing.txt\n',
                'files_changed':['existing.txt'],'changes':{'existing.txt':'after\n'},
                'repository_root':str(root)}
            e=Engine(Path(tmp)/'run.json',workers=workers);e.start('Change existing behavior','brownfield');e.run()
            self.assertEqual(e.state['nodes']['implementation_apply']['status'],'pending')
            self.assertIsNone(e.state['implementation_approval']);self.assertEqual(target.read_text(),'before\n')
            e.approve('reviewer',1);e.run()
            self.assertEqual(e.state['nodes']['implementation_apply']['status'],'passed')
            self.assertEqual(target.read_text(),'after\n');self.assertEqual(e.state['nodes']['release']['status'],'pending')

    def test_applied_agent_changes_can_be_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'repo';root.mkdir();(root/'existing.txt').write_text('before\n')
            agent_script=Path(tmp)/'agent.py'
            agent_script.write_text("import json, pathlib, sys\nrequest=json.loads(pathlib.Path(sys.argv[1]).read_text())\npathlib.Path(request['workspace'],'existing.txt').write_text('changed\\n')\n")
            agent=CommandCodeAgent([sys.executable,str(agent_script)],repository_root=root,
                validation_command=[],apply_changes=True)
            artifact=agent('implementation',{'requirement':'Change behavior','scenario':'brownfield',
                'revision':1,'artifacts':{}})
            CommandCodeAgent.restore_changes(root,artifact['rollback'])
            self.assertEqual((root/'existing.txt').read_text(),'before\n')

    def test_model_agent_applies_provider_generated_change(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                size=int(self.headers['Content-Length']);json.loads(self.rfile.read(size))
                body={'choices':[{'message':{'content':json.dumps({'files':{'generated.txt':'model-generated\n'}})}}]}
                data=json.dumps(body).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)))
                self.end_headers();self.wfile.write(data)
            def log_message(self,*args): pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'repo';root.mkdir()
                agent=OpenAICompatibleCodeAgent('test-key','test-model',
                    'http://127.0.0.1:'+str(server.server_port)+'/v1',repository_root=root,
                    validation_command=[],apply_changes=True)
                artifact=agent('implementation',{'requirement':'Generate behavior','scenario':'greenfield',
                    'revision':1,'artifacts':{}})
                self.assertEqual(artifact['applied_changes'],['generated.txt'])
                self.assertEqual((root/'generated.txt').read_text(),'model-generated\n')
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_security_scanner_rejects_secrets_and_dangerous_operations(self):
        scanner=SecurityScanner();scanner._dependency_audit=lambda root:{'output':'{}','findings':[]}
        result=scanner.scan({'src/Unsafe.cs':'var api_key="hard-coded-secret-value"; Process.Start("cmd");'},Path.cwd())
        self.assertFalse(result['passed'])
        self.assertTrue(any(f['kind']=='secret' for f in result['findings']))
        self.assertTrue(any(f['kind']=='operation' for f in result['findings']))


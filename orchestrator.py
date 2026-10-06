"""Durable governed DAG runner. Workers return structured artifacts, never shell code."""
import concurrent.futures
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GRAPH={'requirements':[], 'design':['requirements'], 'implementation':['design'],
    'security':['design','implementation'], 'implementation_apply':['implementation','security'],
    'tests':['implementation_apply'], 'documentation':['implementation'],
       'readiness':['security','tests','documentation'], 'release':['readiness']}

class SecurityScanner:
    SECRET_PATTERN=re.compile(r'(?i)(api[_-]?key|password|secret|token|connectionstring)\s*[:=]\s*["\'][^"\']{8,}')
    DANGEROUS_PATTERNS=('os.system(', 'subprocess.Popen(', 'Process.Start(', 'Invoke-WebRequest',
                        'curl ', 'wget ', 'rm -rf', 'eval(', 'exec(')
    def scan(self,files,repository_root):
        findings=[]
        for name,text in files.items():
            path=Path(name)
            if path.is_absolute() or '..' in path.parts or path.name=='.env' or path.name.startswith('.env.'):
                findings.append({'severity':'high','kind':'path','file':name,'message':'Generated change targets a protected path'})
            if self.SECRET_PATTERN.search(text):
                findings.append({'severity':'high','kind':'secret','file':name,'message':'Possible hard-coded credential'})
            for term in self.DANGEROUS_PATTERNS:
                if term.lower() in text.lower():
                    findings.append({'severity':'high','kind':'operation','file':name,'message':'Prohibited operation: '+term})
        audit=self._dependency_audit(repository_root)
        findings.extend(audit['findings'])
        return {'passed':not findings,'findings':findings,'checks':['path policy','secret scan','dangerous-operation scan','NuGet vulnerability audit'],
                'dependency_audit':audit['output']}
    @staticmethod
    def _dependency_audit(repository_root):
        command=['dotnet','list','src/UrlShortener.Api/UrlShortener.Api.csproj','package','--vulnerable','--include-transitive','--format','json']
        result=subprocess.run(command,cwd=repository_root,capture_output=True,text=True,timeout=120)
        output=result.stdout[-10000:]
        if result.returncode:
            return {'output':output+result.stderr[-3000:],'findings':[{'severity':'high','kind':'dependency-audit','message':'NuGet vulnerability audit failed'}]}
        try: payload=json.loads(output)
        except json.JSONDecodeError:
            return {'output':output,'findings':[{'severity':'high','kind':'dependency-audit','message':'NuGet audit returned invalid JSON'}]}
        vulnerable=[]
        def walk(value):
            if isinstance(value,dict):
                if any(key in value for key in ('severity','advisoryUrl','advisoryURL')) and value:
                    vulnerable.append(value)
                for child in value.values(): walk(child)
            elif isinstance(value,list):
                for child in value: walk(child)
        walk(payload.get('projects',payload))
        return {'output':output,'findings':[{'severity':'high','kind':'dependency','message':'Vulnerable NuGet package detected','details':item} for item in vulnerable]}

class CommandCodeAgent:
    """Run an external coding agent in a disposable repository copy."""
    def __init__(self,command,repository_root=None,timeout=300,validation_command=None,apply_changes=True):
        if not command: raise ValueError('Coding agent command is required')
        self.command=list(command)
        self.repository_root=Path(repository_root or Path(__file__).resolve().parent).resolve()
        self.timeout=timeout
        self.validation_command=list(validation_command) if validation_command is not None else ['dotnet','build','src/UrlShortener.Api/UrlShortener.Api.csproj']
        self.apply_changes=apply_changes
    def __call__(self,node,context):
        if node!='implementation': raise ValueError('Coding agent only handles implementation')
        workspace=Path(tempfile.mkdtemp(prefix='url-shortener-agent-'))
        try:
            shutil.copytree(self.repository_root,workspace,dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('.git','bin','obj','runs','*.db','.env','.env.*'))
            request=workspace/'.agent-request.json'
            request.write_text(json.dumps({'requirement':context['requirement'],
                'scenario':context['scenario'],'revision':context['revision'],
                'artifacts':context['artifacts'],'workspace':str(workspace)},indent=2))
            result=subprocess.run(self.command+[str(request)],cwd=workspace,capture_output=True,
                                  text=True,timeout=self.timeout)
            if result.returncode:
                raise RuntimeError('Coding agent failed: '+result.stderr[-3000:])
            return self.finalize(workspace)
        finally:
            shutil.rmtree(workspace,ignore_errors=True)
    def finalize(self,workspace):
        patch=self._patch(self.repository_root,workspace)
        if not patch: raise ValueError('Coding agent produced no changes')
        validation_output='validation skipped'
        applied=[]
        rollback={}
        changes=self._changes(self.repository_root,workspace)
        if self.validation_command:
            validation=subprocess.run(self.validation_command,cwd=workspace,capture_output=True,
                                      text=True,timeout=self.timeout)
            if validation.returncode:
                raise RuntimeError('Generated code failed validation: '+validation.stdout[-1500:]+validation.stderr[-1500:])
            validation_output=validation.stdout[-3000:]+validation.stderr[-3000:]
        applied,rollback=self._apply_changes(self.repository_root,workspace) if self.apply_changes else ([],{})
        return {'passed':True,'mode':'isolated external coding agent','files_changed':self._files(patch),
                'patch':patch,'validation':validation_output,'applied_changes':applied,
                'rollback':rollback,'changes':changes,'repository_root':str(self.repository_root)}
    @staticmethod
    def _text_files(root):
        files={}
        for path in root.rglob('*'):
            if not path.is_file() or path.name=='.agent-request.json' or path.name=='.env' or path.name.startswith('.env.') or set(path.relative_to(root).parts)&{'.git','bin','obj','runs'}: continue
            try: files[path.relative_to(root).as_posix()]=path.read_text(encoding='utf-8').splitlines(keepends=True)
            except (UnicodeDecodeError,OSError): pass
        return files
    @classmethod
    def _patch(cls,before_root,after_root):
        before=cls._text_files(before_root); after=cls._text_files(after_root)
        chunks=[]
        for name in sorted(set(before)|set(after)):
            if before.get(name)==after.get(name): continue
            chunks.extend(difflib.unified_diff(before.get(name,[]),after.get(name,[]),
                fromfile='a/'+name,tofile='b/'+name))
        return ''.join(chunks)
    @classmethod
    def _changes(cls,before_root,after_root):
        before=cls._text_files(before_root); after=cls._text_files(after_root)
        return {name:''.join(after[name]) if name in after else None
                for name in sorted(set(before)|set(after)) if before.get(name)!=after.get(name)}
    @classmethod
    def _apply_changes(cls,before_root,after_root):
        before=cls._text_files(before_root); after=cls._text_files(after_root)
        changed=[]
        rollback={}
        for name in sorted(set(before)|set(after)):
            if before.get(name)==after.get(name): continue
            target=(before_root/Path(name)).resolve()
            if before_root.resolve() not in target.parents: raise ValueError('Agent path escaped repository')
            rollback[name]=''.join(before[name]) if name in before else None
            if name in after:
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(''.join(after[name]),encoding='utf-8')
            elif target.exists():
                target.unlink()
            changed.append(name)
        return changed,rollback
    @classmethod
    def apply_snapshot(cls,repository_root,changes):
        root=Path(repository_root).resolve(); applied=[]
        for name,content in changes.items():
            target=(root/Path(name)).resolve()
            if root not in target.parents: raise ValueError('Apply path escaped repository')
            if content is None:
                if target.exists(): target.unlink()
            else:
                target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content,encoding='utf-8')
            applied.append(name)
        return applied
    @classmethod
    def restore_changes(cls,repository_root,rollback):
        root=Path(repository_root).resolve()
        for name,content in rollback.items():
            target=(root/Path(name)).resolve()
            if root not in target.parents: raise ValueError('Rollback path escaped repository')
            if content is None:
                if target.exists(): target.unlink()
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(content,encoding='utf-8')
    @staticmethod
    def _files(patch):
        return sorted({line[6:] for line in patch.splitlines() if line.startswith('+++ b/')})

class OpenAICompatibleCodeAgent(CommandCodeAgent):
    """Use a chat-completions model to edit the isolated workspace."""
    def __init__(self,api_key,model,base_url='https://api.openai.com/v1',**kwargs):
        super().__init__([sys.executable,'-c','pass'],**kwargs)
        if not api_key or api_key.startswith('replace-with-'):
            raise ValueError('Set a real OPENAI_API_KEY in .env or the environment')
        self.api_key=api_key; self.model=model; self.base_url=base_url.rstrip('/')
    def __call__(self,node,context):
        if node!='implementation': raise ValueError('Coding agent only handles implementation')
        workspace=Path(tempfile.mkdtemp(prefix='url-shortener-model-agent-'))
        try:
            shutil.copytree(self.repository_root,workspace,dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('.git','bin','obj','runs','*.db','.env','.env.*'))
            files={name:''.join(lines) for name,lines in self._text_files(workspace).items()}
            prompt=json.dumps({'requirement':context['requirement'],'scenario':context['scenario'],
                'revision':context['revision'],'artifacts':context['artifacts'],'files':files},indent=2)
            request=urllib.request.Request(self.base_url+'/chat/completions',
                data=json.dumps({'model':self.model,'temperature':0,'messages':[
                    {'role':'system','content':'You are a cautious software engineer. Return only valid JSON with a files object mapping repository-relative text paths to complete replacement contents. Preserve existing contracts, make the smallest change, and do not include markdown.'},
                    {'role':'user','content':prompt}]}).encode(),
                headers={'Authorization':'Bearer '+self.api_key,'Content-Type':'application/json'},method='POST')
            with urllib.request.urlopen(request,timeout=self.timeout) as response:
                payload=json.loads(response.read())
            content=payload['choices'][0]['message']['content']
            generated=json.loads(content)['files']
            if not isinstance(generated,dict): raise ValueError('Model response files must be an object')
            for name,text in generated.items():
                target=(workspace/Path(name)).resolve()
                if workspace.resolve() not in target.parents or not isinstance(text,str):
                    raise ValueError('Model returned an invalid file change')
                target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text,encoding='utf-8')
            return self.finalize(workspace)
        except urllib.error.HTTPError as error:
            raise RuntimeError('Model request failed with HTTP '+str(error.code)) from error
        finally:
            shutil.rmtree(workspace,ignore_errors=True)

class GeminiCodeAgent(CommandCodeAgent):
    """Use the Gemini generateContent API to edit the isolated workspace."""
    def __init__(self,api_key,model='gemini-2.0-flash',base_url='https://generativelanguage.googleapis.com/v1beta',**kwargs):
        super().__init__([sys.executable,'-c','pass'],**kwargs)
        if not api_key or api_key.startswith('replace-with-'):
            raise ValueError('Set a real GEMINI_API_KEY in .env or the environment')
        self.api_key=api_key; self.model=model; self.base_url=base_url.rstrip('/')
    def __call__(self,node,context):
        if node!='implementation': raise ValueError('Coding agent only handles implementation')
        workspace=Path(tempfile.mkdtemp(prefix='url-shortener-gemini-agent-'))
        try:
            shutil.copytree(self.repository_root,workspace,dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('.git','bin','obj','runs','*.db','.env','.env.*'))
            files={name:''.join(lines) for name,lines in self._text_files(workspace).items()}
            prompt=json.dumps({'requirement':context['requirement'],'scenario':context['scenario'],
                'revision':context['revision'],'artifacts':context['artifacts'],'files':files},indent=2)
            instruction='Return only valid JSON with a files object mapping repository-relative text paths to complete replacement contents. Preserve contracts, make the smallest change, and do not include markdown.\n'+prompt
            endpoint=self.base_url+'/models/'+urllib.parse.quote(self.model,safe='')+':generateContent?'+urllib.parse.urlencode({'key':self.api_key})
            request=urllib.request.Request(endpoint,data=json.dumps({'contents':[{'parts':[{'text':instruction}]}],
                'generationConfig':{'temperature':0,'responseMimeType':'application/json'}}).encode(),
                headers={'Content-Type':'application/json'},method='POST')
            with urllib.request.urlopen(request,timeout=self.timeout) as response:
                payload=json.loads(response.read())
            content=payload['candidates'][0]['content']['parts'][0]['text']
            generated=json.loads(content)['files']
            if not isinstance(generated,dict): raise ValueError('Gemini response files must be an object')
            for name,text in generated.items():
                target=(workspace/Path(name)).resolve()
                if workspace.resolve() not in target.parents or not isinstance(text,str):
                    raise ValueError('Gemini returned an invalid file change')
                target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text,encoding='utf-8')
            return self.finalize(workspace)
        except urllib.error.HTTPError as error:
            details=error.read().decode('utf-8','replace')[-1500:]
            raise RuntimeError('Gemini request failed with HTTP '+str(error.code)+': '+details) from error
        finally:
            shutil.rmtree(workspace,ignore_errors=True)

class Engine:
    def __init__(self,path,workers=None,agent=None):
        self.path=Path(path)
        self.lock=threading.RLock()
        self.workers=workers or {}
        self.agent=agent
        self.state=json.loads(self.path.read_text()) if self.path.exists() else None
    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(dir=self.path.parent)
        with os.fdopen(fd,'w') as f:
            json.dump(self.state,f,indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,self.path)
    def event(self,kind,**data):
        previous=self.state['audit'][-1]['hash'] if self.state['audit'] else '0'*64
        event={'time':time.time(),'kind':kind,'revision':self.state['revision'],**data,'previous':previous}
        event['hash']=hashlib.sha256(json.dumps(event,sort_keys=True).encode()).hexdigest()
        self.state['audit'].append(event)
        self.save()
    def start(self,requirement,scenario='greenfield'):
        if self.state: raise ValueError('Use a new run path')
        self.state={'revision':1,'requirement':requirement,'scenario':scenario,'started':time.time(),
                    'stopped':False,'approval':None,'implementation_approval':None,'audit':[],'artifacts':{},
                    'nodes':{n:{'status':'pending','attempts':0} for n in GRAPH}}
        self.event('run_created',graph=GRAPH)
    def revise(self,requirement):
        self.restore_applied_changes()
        self.state['revision']+=1
        self.state['requirement']=requirement
        self.state['approval']=None
        self.state['implementation_approval']=None
        self.state['stopped']=False
        # Requirement root reaches every node: invalidation is the transitive closure.
        self.state['nodes']={n:{'status':'pending','attempts':0} for n in GRAPH}
        self.state['artifacts']={}
        self.event('replan',reason='upstream requirement changed; all descendants invalidated')
    def approve(self,reviewer,revision):
        if revision!=self.state['revision']:
            raise ValueError('Approval requires the current revision')
        ambiguities=self.state['artifacts'].get('requirements',{}).get('ambiguities',[])
        if ambiguities: raise ValueError('Approval requires resolved ambiguities: '+ '; '.join(ambiguities))
        if not reviewer.strip(): raise ValueError('Reviewer identity required')
        implementation=self.state['artifacts'].get('implementation',{})
        if implementation.get('patch') and self.state['nodes']['implementation_apply']['status']=='pending':
            self.state['implementation_approval']={'reviewer':reviewer,'revision':revision}
            self.event('implementation_approval',reviewer=reviewer)
            return
        if self.state['nodes']['readiness']['status']!='passed':
            raise ValueError('Release approval requires passed readiness')
        self.state['approval']={'reviewer':reviewer,'revision':revision}
        self.event('human_approval',reviewer=reviewer)
    def stop(self):
        self.restore_applied_changes()
        self.state['stopped']=True
        self.event('safe_stop')
    def restore_applied_changes(self):
        artifact=self.state.get('artifacts',{}).get('implementation_apply',{})
        if not artifact.get('rollback'):
            artifact=self.state.get('artifacts',{}).get('implementation',{})
        rollback=artifact.get('rollback')
        if not rollback: return
        CommandCodeAgent.restore_changes(artifact['repository_root'],rollback)
        artifact['restored']=True
        self.event('restore',node='implementation',files=sorted(rollback))
    def default_worker(self,node,context):
        requirement=context['requirement']
        if node=='requirements':
            return {'intent':requirement,'assumptions':['302 redirects','aggregate UTC daily clicks; no IP retention',
                     'authenticated administration','no destination fetching'],
                    'acceptance':['create valid link','redirect records click','expired link returns 410'],
                    'ambiguities':['What retention is required?','Are custom aliases required?'] if context['scenario']=='ambiguous' and context['revision']==1 else []}
        if node=='design':
            files=['src/UrlShortener.Api/Services/LinkService.cs','src/UrlShortener.Api/Data/InMemoryLinkDataProvider.cs','docs/API.md']
            artifact={'components':['ASP.NET Core API','EF Core data provider','DAG engine'], 'decisions':['process-wide write lock','bounded collision retries'],
                'impacted':files}
            if context['scenario']=='brownfield':
                artifact['baseline_hashes']={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files}
                artifact['compatibility_contract']=['preserve POST /api/links','preserve GET /r/{code}','preserve GET /api/links/{code}/stats']
            return artifact
        if node=='implementation':
            if self.agent:
                apply_changes=self.agent.apply_changes
                self.agent.apply_changes=False
                try: return self.agent(node,context)
                finally: self.agent.apply_changes=apply_changes
            source_files=('src/UrlShortener.Api/Program.cs','src/UrlShortener.Api/Services/LinkService.cs',
                  'src/UrlShortener.Api/Data/InMemoryLinkDataProvider.cs')
            return {'mode':'review existing .NET implementation; deterministic worker does not author arbitrary code',
                'source_hashes':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in source_files}}
        if node=='implementation_apply':
            implementation=context['artifacts'].get('implementation',{})
            changes=implementation.get('changes',{})
            if not changes: return {'passed':True,'mode':'no generated patch; nothing to apply','applied_changes':[]}
            root=Path(implementation['repository_root']).resolve()
            before=CommandCodeAgent._text_files(root)
            rollback={name:''.join(before[name]) if name in before else None for name in changes}
            try:
                applied=CommandCodeAgent.apply_snapshot(root,changes)
            except Exception:
                CommandCodeAgent.restore_changes(root,rollback)
                raise
            return {'passed':True,'mode':'apply validated implementation patch','applied_changes':applied,
                'rollback':rollback,'repository_root':str(root)}
        if node=='tests':
            commands=[([sys.executable,'-m','unittest','discover','-s','tests','-v'],'workflow'),
                      (['dotnet','test','tests/UrlShortener.Api.Tests/UrlShortener.Api.Tests.csproj','--no-restore'],'api')]
            evidence=[]
            for command,name in commands:
                result=subprocess.run(command,capture_output=True,text=True,timeout=120)
                output=result.stdout[-3000:]+result.stderr[-3000:]
                evidence.append({'suite':name,'command':command,'output':output})
                if result.returncode: raise RuntimeError(name+' tests failed: '+output)
            return {'passed':True,'evidence':evidence}
        if node=='security':
            if any(term in requirement.lower() for term in ('disable authentication','store passwords','execute arbitrary')):
                raise ValueError('Policy rejects unsafe requirement')
            implementation=context['artifacts'].get('implementation',{})
            files=implementation.get('changes',{})
            if not files:
                files={name:Path(name).read_text(encoding='utf-8') for name in context['artifacts'].get('design',{}).get('impacted',[]) if Path(name).exists()}
            scan=SecurityScanner().scan(files,Path(__file__).resolve().parent)
            if not scan['passed']: raise ValueError('Security policy rejected: '+json.dumps(scan['findings'])[:3000])
            return {'passed':True,'policy':'path, secret, dangerous-operation, dependency and unsafe-requirement guardrails',
                    'scanned_files':sorted(files),'security_scan':scan}
        if node=='documentation': return {'files':['README.md','docs/ARCHITECTURE.md','docs/API.md'],'passed':all(Path(p).exists() for p in ['README.md','docs/ARCHITECTURE.md','docs/API.md'])}
        if node=='readiness': return {'passed':True,'release_scope':'local release manifest only'}
        return {'approved':context['approval'],'release_manifest':context['artifacts']['implementation'],'deployed':False}
    def execute(self,node):
        started=time.time()
        for attempt in range(1,3):
            with self.lock:
                self.state['nodes'][node]={'status':'running','attempts':attempt}
                self.event('stage_enter',node=node,attempt=attempt)
                context=json.loads(json.dumps(self.state))
            try:
                artifact=self.workers.get(node,self.default_worker)(node,context)
                if not isinstance(artifact,dict) or artifact.get('passed') is False: raise ValueError('Exit gate rejected artifact')
                with self.lock:
                    self.state['artifacts'][node]=artifact
                    self.state['nodes'][node]['status']='passed'
                    self.event('stage_exit',node=node,seconds=time.time()-started,artifact_hash=hashlib.sha256(json.dumps(artifact,sort_keys=True).encode()).hexdigest())
                return
            except Exception as e:
                with self.lock: self.event('stage_error',node=node,error=str(e),attempt=attempt)
        with self.lock:
            self.restore_applied_changes()
            self.state['nodes'][node]['status']='failed'
            self.state['stopped']=True
            self.event('rollback',node=node,action='discard uncommitted stage output; preserve prior artifacts')
            self.event('safe_stop',reason='retry budget exhausted; manual correction required')
    def run(self):
        # Interrupted running stages are replayed: workers must be idempotent.
        for n in self.state['nodes'].values():
            if n['status']=='running': n['status']='pending'
        while not self.state['stopped']:
            ready=[n for n,deps in GRAPH.items() if self.state['nodes'][n]['status']=='pending'
                   and all(self.state['nodes'][d]['status']=='passed' for d in deps)]
            if 'implementation_apply' in ready and self.state['artifacts'].get('implementation',{}).get('patch') \
                    and not self.state['implementation_approval']:
                self.event('approval_required',node='implementation_apply'); break
            if 'release' in ready and not self.state['approval']:
                self.event('approval_required',node='release'); break
            if not ready: break
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(self.execute,ready))
        self.save()
        return self.state
    def metrics(self):
        events=self.state['audit']; errors=[e for e in events if e['kind']=='stage_error']
        recoveries=[]
        for n in GRAPH:
            failures=[e['time'] for e in errors if e['node']==n]
            passes=[e['time'] for e in events if e['kind']=='stage_exit' and e['node']==n]
            if failures and passes and max(passes)>min(failures): recoveries.append(max(passes)-min(failures))
        return {'stage_success_rate':sum(n['status']=='passed' for n in self.state['nodes'].values())/len(GRAPH),
                'retry_count':sum(e['attempt']>1 for e in events if e['kind']=='stage_enter'),
                'rollback_count':sum(e['kind']=='rollback' for e in events),
                'mttr_seconds':sum(recoveries)/len(recoveries) if recoveries else None,
                'elapsed_seconds':events[-1]['time']-self.state['started']}

import argparse
import json
import os
from pathlib import Path
from orchestrator import CommandCodeAgent,Engine,GeminiCodeAgent,OpenAICompatibleCodeAgent

SCENARIOS={'greenfield':'Build a URL shortener with create, redirect, analytics, expiry and authenticated administration.',
'brownfield':'Improve existing click counters to prevent lost updates under concurrent requests; preserve the API.',
'ambiguous':'Make links safer and analytics more useful. Proposed interpretation: seven-day expiry and anonymous daily counts; retention needs owner confirmation.'}

def load_env_file(path='.env'):
    env_path=Path(path)
    if not env_path.exists(): return
    for line in env_path.read_text().splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        key,value=line.split('=',1); value=value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(),value)

if __name__=='__main__':
    load_env_file()
    p=argparse.ArgumentParser();p.add_argument('scenario',choices=SCENARIOS);p.add_argument('--approve',help='Human reviewer identity after review');p.add_argument('--revise');p.add_argument('--stop',action='store_true');p.add_argument('--state-dir',default='runs');p.add_argument('--agent-command',nargs='+',help='External coding agent command; request JSON path is appended');p.add_argument('--model-agent',action='store_true',help='Use an OpenAI-compatible coding model');p.add_argument('--gemini-agent',action='store_true',help='Use Gemini for coding generation')
    args=p.parse_args()
    if args.gemini_agent:
        agent=GeminiCodeAgent(os.environ.get('GEMINI_API_KEY'),os.environ.get('GEMINI_MODEL','gemini-2.0-flash'))
    elif args.model_agent:
        agent=OpenAICompatibleCodeAgent(os.environ.get('OPENAI_API_KEY'),os.environ.get('OPENAI_MODEL','gpt-4.1-mini'),os.environ.get('OPENAI_BASE_URL','https://api.openai.com/v1'))
    else: agent=CommandCodeAgent(args.agent_command) if args.agent_command else None
    e=Engine(Path(args.state_dir)/(args.scenario+'.json'),agent=agent)
    if not e.state:e.start(SCENARIOS[args.scenario],args.scenario)
    if args.revise:e.revise(args.revise)
    if args.stop:e.stop()
    e.run()
    if args.approve:e.approve(args.approve,e.state['revision']);e.run()
    print(json.dumps({'nodes':e.state['nodes'],'metrics':e.metrics(),'state':str(e.path)},indent=2))

"""Foreground source-only launcher; no installation, process killing or data migration."""
from pathlib import Path
import os,sys,shutil,argparse,subprocess,socket
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('role',choices=['api','worker','web']);p.add_argument('--web-port',type=int,default=3610);p.add_argument('--api-port',type=int,default=8610);args=p.parse_args()
data=ROOT/'评审数据';py=ROOT/'.venv/Scripts/python.exe';node=shutil.which('node')
if not py.is_file():raise SystemExit('请先在此源码目录建立Python3.12环境并安装requirements依赖；普通评审可使用随包运行版。')
env=os.environ.copy();env.update({'NATURE_WORKBENCH_HOME':str(data/'Workbench/appdata'),'NATURE_PROJECTS_ROOT':str(data/'Workbench/projects'),'PAPER_DATA_ROOT':str(data/'Paper/data'),'PAPER_BACKUP_ROOT':str(data/'Paper/backup'),'PAPER_DISABLE_AUTO_COPY':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(ROOT),'MPLCONFIGDIR':str(data/'缓存/matplotlib'),'TEMP':str(data/'临时'),'TMP':str(data/'临时'),'NATURE_FRONTEND_PORT':str(args.web_port),'PORT':str(args.web_port),'NATURE_API_URL':'http://127.0.0.1:'+str(args.api_port)})
for rel in ['缓存/matplotlib','临时']:(data/rel).mkdir(parents=True,exist_ok=True)
if args.role in ['api','web']:
    with socket.socket() as s:
        try:s.bind(('127.0.0.1',args.api_port if args.role=='api' else args.web_port))
        except OSError:raise SystemExit('端口被占用；未停止任何其他程序。请关闭自己的实例或选择新端口。')
if args.role=='api':
    subprocess.run([str(py),'-B','-c','from backend import db; from Paper.paper_app import db as p; db.init_db(); p.init_db()'],cwd=ROOT,env=env,check=True)
    cmd=[str(py),'-B','-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',str(args.api_port)]
elif args.role=='worker':cmd=[str(py),'-B','-m','backend.worker']
else:
    if not node or not (ROOT/'dist/server/index.js').is_file():raise SystemExit('需要可用Node以及本目录完成的生产构建。')
    cmd=[node,'scripts/local-server.mjs']
raise SystemExit(subprocess.call(cmd,cwd=ROOT,env=env))

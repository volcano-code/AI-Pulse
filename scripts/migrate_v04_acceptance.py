"""Upgrade a disposable database created by actual v0.3 source to v0.4.
Never mutates a user database. Old-table row values must remain identical.
"""
import argparse, hashlib, json, os, sqlite3, subprocess, sys, tempfile
from pathlib import Path

def snapshot(path):
    with sqlite3.connect(path) as db:
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'")]
        out={}
        for name in tables:
            rows=db.execute('SELECT * FROM "'+name+'"').fetchall()
            serial=sorted(json.dumps(row,ensure_ascii=False,sort_keys=True) for row in rows)
            out[name]={'rows':len(rows),'sha256':hashlib.sha256('\n'.join(serial).encode()).hexdigest()}
        return out

def main():
    p=argparse.ArgumentParser();p.add_argument('--baseline',required=True);p.add_argument('--output',default='docs/validation-v0.4/migration-v03-v04.json');args=p.parse_args()
    root=Path(__file__).resolve().parents[1];baseline=Path(args.baseline).resolve()
    if not (baseline/'backend/app').exists():raise SystemExit('Expected extracted original ai-pulse v0.3 root')
    log=[]
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/'upgrade.db';env={**os.environ,'DATABASE_URL':'sqlite:///'+str(path),'DATA_MODE':'replay','LLM_MODE':'extractive','TASK_MODE':'local','DELIVERY_TRANSPORT':'file'}
        def run(project,*cmd):
            r=subprocess.run([sys.executable,*cmd],cwd=project/'backend',env=env,capture_output=True,text=True,timeout=40)
            log.append({'command':list(cmd),'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
            if r.returncode:raise RuntimeError(r.stderr)
        run(baseline,'-m','alembic','upgrade','head');run(baseline,'-m','app.cli','demo')
        before=snapshot(path);run(root,'-m','alembic','upgrade','head');after=snapshot(path)
        assert all(after[t]==v for t,v in before.items()),'Old data changed during v0.3 -> v0.4 migration'
        assert {'events','event_articles','event_versions'} <= after.keys()
        run(root,'-m','alembic','check')
        run(root,'-m','alembic','downgrade','0003');assert all(snapshot(path)[t]==v for t,v in before.items())
        run(root,'-m','alembic','upgrade','head');assert all(snapshot(path)[t]==v for t,v in before.items())
    result={'status':'passed','baseline':'actual v0.3.0 source','target':'0004','old_tables_before':before,'old_tables_after':{t:after[t] for t in before},'new_tables':['events','event_articles','event_versions'],'steps':log,'scope':'disposable SQLite only; PostgreSQL/Docker still unverified'}
    out=Path(args.output);out=out if out.is_absolute() else root/out;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2,ensure_ascii=False));print('PASS: v0.3 rows preserved; v0.4 event tables created; Alembic round trip succeeded.')
if __name__=='__main__':main()

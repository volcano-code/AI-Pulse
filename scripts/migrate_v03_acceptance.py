"""Upgrade a disposable database CREATED WITH actual v0.2 code. Never accepts
or mutates the user's live database. All old-table row values must remain identical.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile


def snapshot(path):
    with sqlite3.connect(path) as db:
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'")]
        result={}
        for name in tables:
            rows=db.execute('SELECT * FROM "'+name+'"').fetchall()
            serialized=sorted(json.dumps(row,ensure_ascii=False,sort_keys=True) for row in rows)
            result[name]={'rows':len(rows),'sha256':hashlib.sha256('\n'.join(serialized).encode()).hexdigest()}
        return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--baseline',required=True);p.add_argument('--output',default='docs/validation-v0.3/migration.json');args=p.parse_args()
    root=Path(__file__).resolve().parents[1];baseline=Path(args.baseline).resolve()
    if not (baseline/'backend/app').exists():raise SystemExit('Expected extracted original ai-pulse v0.2 root')
    log=[]
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/'upgrade.db'
        env={**os.environ,'DATABASE_URL':'sqlite:///'+str(path),'DATA_MODE':'replay','LLM_MODE':'extractive','TASK_MODE':'local','DELIVERY_TRANSPORT':'file'}
        def run(cwd,*cmd):
            result=subprocess.run([sys.executable,*cmd],cwd=cwd/'backend',env=env,capture_output=True,text=True,timeout=25)
            log.append({'command':list(cmd),'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
            if result.returncode:raise RuntimeError(result.stderr)
        run(baseline,'-m','alembic','upgrade','head')
        run(baseline,'-m','app.cli','demo')
        before=snapshot(path)
        run(root,'-m','alembic','upgrade','head')
        after=snapshot(path)
        assert all(after[t]==values for t,values in before.items()),'Old data changed during upgrade'
        assert {'reading_states','investigations'} <= after.keys()
        run(root,'-m','alembic','check')
        # Test a disposable round trip only; never a production downgrade recipe.
        run(root,'-m','alembic','downgrade','0002')
        run(root,'-m','alembic','upgrade','head')
        assert all(snapshot(path)[t]==values for t,values in before.items())
    result={'status':'passed','baseline':'actual v0.2.0 source','target':'0003','old_tables_before':before,'old_tables_after':{t:after[t] for t in before},'new_tables':['reading_states','investigations'],'steps':log,'scope':'disposable SQLite only; not PostgreSQL, Docker, or hot migration'}
    out=Path(args.output);out=out if out.is_absolute() else root/out;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2,ensure_ascii=False))
    print('PASS: old rows preserved, new tables created, Alembic check and disposable round trip succeeded.')

if __name__=='__main__':main()

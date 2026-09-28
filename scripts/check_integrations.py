"""Reproducible readiness checks; never calls a paid model or sends SMTP mail.

--feeds attempts the three configured public sources in a temporary LIVE database,
with process deadlines. Failed sources never receive synthetic replacement data.
"""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--feeds',action='store_true')
    parser.add_argument('--output',default='docs/validation-v0.4/integrations.json')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'backend'))
    from app.config import Settings
    from app.db import Base,make_database
    from app.bootstrap import bootstrap
    from app.models import Source,Article
    from app.ingestion import fetch_source
    from app.process_fetcher import ProcessFetcher
    from sqlalchemy import select,func
    report={'checked_at_utc':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
            'python':sys.version.split()[0],'node':None,'docker_available':bool(shutil.which('docker')),
            'paid_model_calls':0,'smtp_submissions':0,'dependencies':{},'feed_checks':[]}
    for name in ['fastapi','pydantic','sqlalchemy','httpx','alembic','langgraph','celery','redis']:
        try:report['dependencies'][name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:report['dependencies'][name]=None
    if shutil.which('node'):
        report['node']=subprocess.run(['node','--version'],capture_output=True,text=True,timeout=5).stdout.strip()
    if args.feeds:
        with tempfile.TemporaryDirectory() as temp:
            settings=Settings(_env_file=None,database_url='sqlite:///'+str(Path(temp)/'live.db'),data_mode='live',llm_mode='extractive',delivery_transport='file',fetch_deadline_seconds=3,fetch_timeout_seconds=1)
            engine,factory=make_database(settings);Base.metadata.create_all(engine);bootstrap(factory,settings)
            with factory() as db:sources=list(db.scalars(select(Source.id).order_by(Source.id)))
            for source in sources:
                start=time.monotonic()
                result=fetch_source(factory,source,settings,ProcessFetcher(settings))
                report['feed_checks'].append(result|{'elapsed_seconds':round(time.monotonic()-start,3)})
            with factory() as db:report['live_articles_saved']=db.scalar(select(func.count()).select_from(Article))
            engine.dispose()
        report['live_feeds_gate']='passed' if all(x['status']=='succeeded' for x in report['feed_checks']) else 'blocked_or_failed'
    report['nextjs_build']='not_validated' if not (root/'apps/web/.next/BUILD_ID').exists() else 'build_artifact_present_not_certified'
    output=Path(args.output)
    if not output.is_absolute():output=root/output
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2,ensure_ascii=False))
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return 1 if args.feeds and report['live_feeds_gate']!='passed' else 0

if __name__=='__main__':raise SystemExit(main())

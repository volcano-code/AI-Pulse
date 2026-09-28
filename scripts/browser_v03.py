"""Actual local UI + actual API/SQLite acceptance. --bridge explicitly bypasses
browser networking via HTTPX only when Chromium native networking is unavailable.
It is not a Next.js build/E2E test. All content is synthetic replay content.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import time
import httpx
from playwright.sync_api import sync_playwright, expect


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--bridge',action='store_true')
    p.add_argument('--base-url',default='http://127.0.0.1:8000')
    args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    output=root/'docs/validation-v0.3';screens=root/'docs/screenshots/v0.3'
    output.mkdir(exist_ok=True);screens.mkdir(exist_ok=True)
    base=args.base_url.rstrip('/')
    status=httpx.get(base+'/api/v1/status',trust_env=False).json()
    if status['data_mode']!='replay':raise SystemExit('Only the explicit synthetic replay instance may be tested')
    tests=[];errors=[]
    def record(name):tests.append({'name':name,'status':'passed'})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium'))
        page=browser.new_page(viewport={'width':1440,'height':1050})
        page.set_default_timeout(6000)
        page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            if args.bridge:
                static=root/'backend/app/static'
                html=(static/'index.html').read_text()
                html=re.sub(r'<link rel="stylesheet"[^>]+>','',html)
                html=html.replace('<script src="/static/app.js" defer></script>','')
                def bridge(source,path,options):
                    if not path.startswith('/api/v1/') or '..' in path:raise ValueError('Out-of-scope path')
                    r=httpx.request(options.get('method','GET'),base+path,headers=options.get('headers',{}),content=options.get('body'),timeout=30,trust_env=False)
                    return {'body':r.text,'status':r.status_code,'headers':dict(r.headers)}
                page.expose_binding('__pulseHttp',bridge)
                page.set_content(html)
                page.add_style_tag(content=(static/'app.css').read_text())
                page.evaluate('''() => {const d=new Map();Object.defineProperty(window,'sessionStorage',{value:{getItem:k=>d.get(k)||null,setItem:(k,v)=>d.set(k,String(v)),removeItem:k=>d.delete(k)}});window.fetch=async(path,options={})=>{const r=await window.__pulseHttp(path,options);return new Response(r.body,{status:r.status,headers:r.headers});};}''')
                page.add_script_tag(content=(static/'app.js').read_text())
            else:
                page.goto(base,timeout=7000)
            expect(page.locator('.article')).to_have_count(6)
            expect(page.locator('#mode')).to_contain_text('REPLAY')
            record('six database-backed synthetic cards and visible REPLAY marker')
            page.screenshot(path=str(screens/'dashboard.png'))
            page.get_by_role('button',name='▧ 查看来源与证据 ↗').first.click()
            expect(page.locator('#evidence-dialog mark')).not_to_be_empty()
            assert page.locator('#evidence-dialog mark').inner_text()==page.locator('#evidence-dialog .quote').inner_text()
            record('existing evidence dialog still highlights exact source text')
            page.get_by_role('button',name='关闭证据').click()
            page.locator('[data-view=updates]').click()
            expect(page.locator('[data-update]')).to_have_count(6)
            record('new read-version page loads real API data')
            article=page.locator('[data-read]').first.get_attribute('data-read')
            snapshot=page.locator('[data-read]').first.get_attribute('data-snapshot')
            page.locator('[data-read]').first.click()
            expect(page.locator(f'[data-update="{article}"] .pill')).to_have_text('本版已读')
            data=httpx.get(base+f'/api/v1/articles/{article}/changes',trust_env=False).json()
            assert data['baseline_snapshot_id']==snapshot
            record('read button writes the exact displayed snapshot to SQLite')
            # Simulate a source update THROUGH the real ingestion function, never through HTTP test routes.
            sys.path.insert(0,str(root/'backend'))
            from app.config import Settings
            from app.db import make_database
            from app.models import Article,Source,Snapshot
            from app.feeds import Entry
            from app.ingestion import ingest_entries
            from app.timeutil import iso,utcnow
            db_settings=Settings(_env_file=None,database_url='sqlite:///'+(root/'data/replay.db').as_posix(),data_mode='replay')
            engine,factory=make_database(db_settings)
            with factory.begin() as db:
                a=db.get(Article,article);old=db.get(Snapshot,snapshot)
                ingest_entries(db,db.get(Source,a.source_id),[Entry(old.title,a.canonical_url,old.text+'\n【v0.3 合成更新】新增工具调用预算与逐步审计记录；这不是实际新闻。',a.published_at,iso(utcnow()))],'replay')
            engine.dispose()
            page.locator('[data-view=updates]').click()
            expect(page.locator(f'[data-update="{article}"] .pill')).to_have_text('已读后有变化')
            page.locator('#changed-only').check()
            expect(page.locator('[data-update]')).to_have_count(1)
            record('same-URL source update is distinguished from already-read version')
            page.locator('[data-diff]').first.click()
            expect(page.locator('.diff-line.added')).to_contain_text('v0.3 合成更新')
            expect(page.locator('.delta-panel')).to_contain_text('不是语义事件聚类')
            page.screenshot(path=str(screens/'changes.png'),full_page=True)
            record('text differences are visible and explicitly not labelled verified event facts')
            page.locator('[data-unread]').first.click()
            expect(page.locator(f'[data-update="{article}"] .pill')).to_have_text('未读')
            record('unread action reverses the read marker')
            page.locator('[data-view=agent]').click()
            page.locator('#agent-question').fill('Agent 工作流如何处理预算与证据？')
            page.locator('#agent-submit').click()
            expect(page.locator('#agent-current .evidence-block').first).to_be_visible(timeout=10000)
            expect(page.locator('#agent-current .agent-counts')).to_contain_text('模型调用 0')
            expect(page.locator('#agent-current .timeline')).to_contain_text('search_saved_evidence')
            record('research executes local tools, displays evidence and accurately reports zero model calls')
            assert httpx.get(base+'/api/v1/investigations',trust_env=False).json()[0]['status']=='completed'
            record('completed investigation and tool audit are persisted in the backend')
            page.screenshot(path=str(screens/'research.png'),full_page=True)
            page.locator('[data-view=sources]').click()
            page.locator('[data-view=agent]').click()
            page.locator('[data-investigation]').first.click()
            expect(page.locator('#agent-current .evidence-block').first).to_be_visible()
            record('saved research can be reopened after navigating away')
            page.locator('#agent-question').fill('qzxunmatchedkeyword')
            page.locator('#agent-submit').click()
            expect(page.locator('#agent-current')).to_contain_text('没有匹配到足够证据')
            expect(page.locator('#agent-current .evidence-block')).to_have_count(0)
            record('unanswerable question returns no invented citations')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth')<=390
            page.screenshot(path=str(screens/'mobile.png'),full_page=True)
            record('research page has no horizontal overflow at 390px width')
            assert not errors,errors
            record('no uncaught JavaScript errors')
        except Exception as exc:
            (output/('browser-bridge.json' if args.bridge else 'browser-native.json')).write_text(json.dumps({'transport':'python-httpx-bridge' if args.bridge else 'native-browser-http','tests':tests,'errors':errors,'status':'failed','error':str(exc)},ensure_ascii=False,indent=2))
            raise
        finally:browser.close()
    report={'transport':'python-httpx-bridge' if args.bridge else 'native-browser-http','frontend':'FastAPI native JS acceptance UI, NOT Next.js','data':'synthetic replay','tests':tests,'errors':errors,'status':'passed'}
    (output/('browser-bridge.json' if args.bridge else 'browser-native.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

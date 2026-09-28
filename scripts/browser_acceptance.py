"""Acceptance checks for the native local UI, not the unbuilt Next.js frontend.
--bridge renders original UI code, forwarding fetch via Python to the REAL local API.
It is useful in a browser with network disabled. It is NOT native-network E2E.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import time
import httpx
from playwright.sync_api import sync_playwright, expect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    parser.add_argument('--bridge',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    output=root/'docs'/'validation-v0.2';screens=root/'docs'/'screenshots'/'v0.2'
    output.mkdir(parents=True,exist_ok=True);screens.mkdir(parents=True,exist_ok=True)
    base=args.base_url.rstrip('/')
    records=[];errors=[]
    def record(name):
        records.append({'case':name,'status':'passed'})
    original_prefs=httpx.get(base+'/api/v1/preferences',trust_env=False).json()
    original_schedule=httpx.get(base+'/api/v1/automation',trust_env=False).json()['schedule']
    with sync_playwright() as p:
        options={'headless':True}
        if shutil.which('chromium'):
            options['executable_path']=shutil.which('chromium')
        browser=p.chromium.launch(**options)
        page=browser.new_page(viewport={'width':1440,'height':1100})
        page.on('pageerror',lambda e:errors.append(str(e)))
        try:
            if args.bridge:
                static=root/'backend'/'app'/'static'
                html=(static/'index.html').read_text(encoding='utf-8')
                html=re.sub(r'<link rel="stylesheet"[^>]+>','',html)
                html=html.replace('<script src="/static/app.js" defer></script>','')
                def bridge(source,path,options):
                    if not path.startswith('/api/v1/') or '..' in path:
                        raise ValueError('Only this app API is allowed in the test bridge')
                    r=httpx.request(options.get('method','GET'),base+path,headers=options.get('headers',{}),content=options.get('body'),timeout=30,trust_env=False)
                    return {'body':r.text,'status':r.status_code,'headers':dict(r.headers)}
                page.expose_binding('__pulseHttp',bridge)
                page.set_content(html)
                page.add_style_tag(content=(static/'app.css').read_text(encoding='utf-8'))
                page.evaluate("""() => {const d=new Map();Object.defineProperty(window,'sessionStorage',{value:{getItem:k=>d.get(k)||null,setItem:(k,v)=>d.set(k,String(v)),removeItem:k=>d.delete(k)}});window.fetch=async(path,options={})=>{const r=await window.__pulseHttp(path,options);return new Response(r.body,{status:r.status,headers:r.headers});};}""")
                page.add_script_tag(content=(static/'app.js').read_text(encoding='utf-8'))
            else:
                page.goto(base)
            expect(page.locator('.article')).to_have_count(6)
            expect(page.locator('#mode')).to_contain_text('REPLAY')
            record('replay marker and six real database-backed cards')
            page.screenshot(path=str(screens/'dashboard.png'))
            page.get_by_role('button',name='▧ 查看来源与证据 ↗').first.click()
            expect(page.locator('#evidence-dialog mark')).not_to_be_empty()
            quote=page.locator('#evidence-dialog .quote').inner_text()
            assert page.locator('#evidence-dialog mark').inner_text()==quote
            record('evidence dialog highlights the exact cited substring')
            page.screenshot(path=str(screens/'evidence.png'))
            page.get_by_role('button',name='关闭证据').click()
            expect(page.locator('#evidence-dialog')).not_to_be_visible()
            record('accessible evidence dialog closes correctly')
            page.locator('[data-bookmark]').first.click()
            expect(page.locator('[data-bookmark]').first).to_have_text('★ 已收藏')
            assert httpx.get(base+'/api/v1/articles?bookmarked=true',trust_env=False).json()
            page.locator('[data-bookmark]').first.click()
            expect(page.locator('[data-bookmark]').first).to_have_text('☆ 收藏')
            record('bookmark writes persist in the backend and are reversible')
            page.locator('[data-view=sources]').click()
            page.locator('[data-toggle-source=arxiv]').click()
            expect(page.locator('[data-toggle-source=arxiv]')).to_have_text('启用来源')
            page.locator('[data-toggle-source=arxiv]').click()
            expect(page.locator('[data-toggle-source=arxiv]')).to_have_text('停用来源')
            record('source enabled state persists and toggles back')
            page.locator('[data-view=settings]').click()
            page.locator('input[name=timezone]').fill('Asia/Shanghai')
            page.get_by_role('button',name='保存偏好',exact=True).click()
            expect(page.locator('#toast')).to_contain_text('偏好已保存')
            assert httpx.get(base+'/api/v1/preferences',trust_env=False).json()['timezone']=='Asia/Shanghai'
            record('timezone preference saves through the API')
            httpx.put(base+'/api/v1/preferences',json=original_prefs,trust_env=False).raise_for_status()
            page.locator('[data-view=research]').click()
            page.get_by_label('检索问题').fill('Agent 工作流')
            page.get_by_role('button',name='检索证据 ↗',exact=True).click()
            expect(page.locator('#answer blockquote').first).to_be_visible()
            record('SSE protocol is consumed and displays cited snippets')
            page.get_by_label('检索问题').fill('qzxunmatchedkeyword')
            page.get_by_role('button',name='检索证据 ↗',exact=True).click()
            expect(page.locator('#answer')).to_contain_text('没有匹配到足够证据')
            expect(page.locator('#answer blockquote')).to_have_count(0)
            record('unmatched question abstains instead of fabricating an answer')
            page.locator('[data-view=history]').click()
            page.locator('[data-edition]').first.click()
            expect(page.locator('.article')).to_have_count(6)
            record('historical edition loads saved items')
            page.locator('[data-action=generate]').click()
            expect(page.locator('#toast')).to_contain_text('任务完成',timeout=15000)
            page.locator('[data-view=runs]').click()
            expect(page.locator('.timeline').first).to_contain_text(re.compile('任务完成|Committed results'))
            record('generation task completes and exposes persisted run events')
            page.locator('[data-view=brief]').click()
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth')<=390
            page.screenshot(path=str(screens/'mobile.png'),full_page=True)
            record('390px mobile viewport has no horizontal overflow')
            page.set_viewport_size({'width':1440,'height':1100})
            page.locator('[data-view=automation]').click()
            expect(page.locator('#content')).to_contain_text('LOCAL FILE')
            expect(page.locator('#content')).to_contain_text('在线')
            record('automation view distinguishes local file preview and reports worker heartbeat')
            page.locator('#schedule-form input[name=timezone]').fill('Asia/Shanghai')
            page.locator('#schedule-form input[name=local_time]').fill('08:15')
            page.locator('#schedule-form input[name=send]').check()
            page.get_by_role('button',name='保存调度',exact=True).click()
            expect(page.locator('#toast')).to_contain_text('调度设置已保存')
            saved=httpx.get(base+'/api/v1/automation',trust_env=False).json()['schedule']
            assert saved['timezone']=='Asia/Shanghai' and saved['local_time']=='08:15' and saved['send']
            record('daily schedule settings persist through the actual API')
            page.locator('#daily-now').click()
            expect(page.locator('#toast')).to_contain_text('每日流程已入队')
            for _ in range(40):
                deliveries=httpx.get(base+'/api/v1/deliveries',trust_env=False).json()
                if deliveries and deliveries[0]['status']=='file_written':
                    break
                time.sleep(.25)
            else:
                raise AssertionError('Independent worker did not produce local preview')
            page.get_by_role('button',name='刷新状态',exact=True).click()
            expect(page.locator('#content')).to_contain_text('file_written')
            record('independent worker completes daily job and writes local email preview')
            preview=httpx.get(base+f"/api/v1/deliveries/{deliveries[0]['id']}/preview",trust_env=False)
            assert preview.status_code==200 and 'SYNTHETIC' in preview.text
            record('preview artifact is served with a synthetic-data marker')
            page.screenshot(path=str(screens/'automation.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth')<=390
            page.screenshot(path=str(screens/'automation-mobile.png'),full_page=True)
            record('automation view fits 390px mobile viewport')
            assert not errors,errors
            record('no uncaught JavaScript errors during all interactions')
            assert page.evaluate("cpSlice('A😀中文B', 2, 5)")=="中文B"
            record('Unicode code-point slicing agrees with backend offsets')
        except Exception as exc:
            records.append({'case':'acceptance failure','status':'failed','error':str(exc)})
            raise
        finally:
            httpx.put(base+'/api/v1/preferences',json=original_prefs,trust_env=False)
            httpx.put(base+'/api/v1/automation/schedule',json={k:v for k,v in original_schedule.items() if k!='updated_at'},trust_env=False)
            result={'ui':'backend/app/static (NOT Next.js)','transport':'Python HTTP bridge to real local API; browser networking disabled' if args.bridge else 'native browser HTTP','passed':sum(x['status']=='passed' for x in records),'failed':sum(x['status']=='failed' for x in records),'uncaught_js_errors':errors,'cases':records}
            (output/'browser-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False,indent=2))
            browser.close()

if __name__=='__main__':
    main()

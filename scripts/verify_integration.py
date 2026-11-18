"""Read-only git ancestry gate for the reviewed integration stack. No network."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/'docs/INTEGRATION-MANIFEST.json'


def verify(manifest, repo=ROOT):
    if manifest.get('version')!=1 or manifest.get('repository')!='volcano-code/AI-Pulse':
        raise ValueError('Unsupported integration manifest')
    rows=manifest.get('pull_requests',[])
    if [r.get('number') for r in rows]!=list(range(24,36)):
        raise ValueError('Expected exactly the reviewed PR24–35 stack')
    expected_base=manifest['main_base']
    for row in rows:
        if row.get('base_sha')!=expected_base:
            raise ValueError(f"Broken stack link at PR{row['number']}")
        expected_base=row['head']
    if expected_base!=manifest['stack_tip']:
        raise ValueError('Stack tip mismatch')
    refs=[manifest['main_base']]+[r['head'] for r in rows]
    for sha in refs:
        if not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{40}',sha):
            raise ValueError('Full immutable SHA required')
        result=subprocess.run(['git','merge-base','--is-ancestor',sha,'HEAD'],cwd=repo,capture_output=True,text=True)
        if result.returncode:
            raise ValueError(f'Reviewed commit {sha} missing from HEAD ancestry; use full-history checkout')
    for parent,child in zip(refs,refs[1:]):
        result=subprocess.run(['git','merge-base','--is-ancestor',parent,child],cwd=repo,capture_output=True,text=True)
        if result.returncode:
            raise ValueError('Manifest stack does not match actual git ancestry')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    return dict(passed=True,head=head,reviewed_main_base=manifest['main_base'],reviewed_stack_tip=manifest['stack_tip'],
        included_pull_requests=[r['number'] for r in rows],
        limit='Ancestry proves inclusion, not absence of later reversions or correctness; run tests and review diff')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    report=json.dumps(verify(json.loads(MANIFEST.read_text())),indent=2)+'\n'
    if args.output:args.output.write_text(report,encoding='utf-8')
    else:print(report,end='')


if __name__=='__main__':main()

"""Offline public-source excerpt smoke test; no live refresh or provider calls."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
from .research_eval import evaluate, load_benchmark

DEFAULT_DATASET=Path(__file__).resolve().parents[1]/'tests/fixtures/research_eval_public_nist_v1.json'
POLICY='https://www.nist.gov/copyrights-disclaimers'


def load_public_dataset(path):
    data=load_benchmark(path,expected_provenance='public_excerpts')
    if data.get('label_origin')!='ai_authored_not_human_adjudicated' or data.get('split')!='public_smoke_not_holdout':
        raise ValueError('Public data and unreviewed label provenance must be explicit')
    urls=set()
    for doc in data['documents']:
        url=doc.get('source_url',''); parsed=urlparse(url)
        if parsed.scheme!='https' or parsed.hostname not in {'www.nist.gov','csrc.nist.gov'} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Only vetted canonical NIST sources are registered')
        if url in urls:
            raise ValueError('One bounded excerpt per source page')
        urls.add(url)
        if not 1<=len(doc['text'].split())<=25:
            raise ValueError('Excerpt must contain 1–25 words; not a full-page archive')
        if doc.get('excerpt_sha256')!=hashlib.sha256(doc['text'].encode()).hexdigest():
            raise ValueError('Excerpt integrity mismatch')
        for key in ['source_attribution','source_locator','retrieved_on','extraction','title_origin']:
            if not isinstance(doc.get(key),str) or not doc[key].strip():
                raise ValueError('Source provenance is incomplete')
        rights=doc.get('rights',{})
        if (rights.get('policy_url')!=POLICY or not rights.get('basis') or not rights.get('review_kind')
                or rights.get('copyrighted_third_party_material') is not False):
            raise ValueError('Explicit reuse basis and third-party exclusion required')
    return data


def run(path=DEFAULT_DATASET):
    data=load_public_dataset(path)
    report=evaluate(path,dataset_loader=load_public_dataset)
    report.update(human_review_completed=False,source_refresh='not_run; frozen visible-text excerpts',
        benchmark_scope='real public excerpts, AI-authored questions and labels, tiny single-publisher smoke test',
        sources=[{k:d[k] for k in ['id','source_url','source_attribution','source_locator','retrieved_on','excerpt_sha256','rights','extraction','title_origin']} for d in data['documents']],
        excerpt_count=len(data['documents']),stored_excerpt_words=sum(len(d['text'].split()) for d in data['documents']))
    report['limitations'] += ['No-answer means absent from saved excerpts, not absent from full source pages',
        'Rights metadata is an AI-assisted review record, not automatic license certification or legal advice',
        'Provenance public_excerpts is independent of replay database namespace; no synthetic document is mixed in',
        'Hash checks prove saved-byte integrity, not continuing website equality or factual truth',
        'Citation offsets are relative to saved excerpts, not original HTML or full-page text']
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(); content=json.dumps(run(),ensure_ascii=False,indent=2)+'\n'
    if args.output:args.output.write_text(content,encoding='utf-8')
    else:print(content,end='')


if __name__=='__main__':main()

"""Offline evidence benchmark; synthetic by default. No DB URL or provider calls."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from .db import Base
from .models import Article, Snapshot, Source, Brief, BriefItem
from .research import citation_matches_snapshot
from .research_retrieval import search_research_evidence
from .retrieval_eval import recall_at_k, reciprocal_rank

DEFAULT_DATASET = Path(__file__).resolve().parents[1] / 'tests/fixtures/research_eval_synthetic_v2.json'


def load_benchmark(path, *, expected_provenance="synthetic"):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('provenance') != expected_provenance or not data.get('label_origin'):
        raise ValueError('Dataset provenance must match the explicitly selected loader')
    docs, cases = data['documents'], data['cases']
    ids = [d['id'] for d in docs]
    if not docs or len(ids) != len(set(ids)) or not cases:
        raise ValueError('Empty corpus/cases or duplicate document IDs')
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Duplicate case IDs')
    historical = set(data['historical_snapshot_ids'])
    if not historical <= set(ids):
        raise ValueError('Unknown historical snapshot')
    for case in cases:
        relevant = case['relevant_snapshot_ids']
        if (case['scope'] not in {'current', 'historical'} or not case['question'].strip()
                or not set(relevant) <= set(ids) or len(set(relevant)) != len(relevant)):
            raise ValueError('Invalid case or relevance labels')
    return data


def seed(db, data):
    """Caller owns a newly created memory-only DB; no application configuration read."""
    for row in data['documents']:
        aid = row['article']
        if db.get(Article, aid) is None:
            db.add(Source(id=aid, name=aid, url=row.get('source_url', f'https://example.invalid/{aid}'),
                          kind='rss', enabled=row.get('enabled', True)))
            db.flush()
            db.add(Article(id=aid, source_id=aid, canonical_url=row.get('source_url', f'https://example.invalid/{aid}'),
                           title=row['title'], data_mode=row.get('data_mode', 'replay'),
                           published_at=row['published_at']))
            db.flush()
        db.add(Snapshot(id=row['id'], article_id=aid, title=row['title'], text=row['text'],
                        content_hash=hashlib.sha256(row['text'].encode()).hexdigest(),
                        captured_at=row['published_at']))
        db.flush()
        if row.get('current', True):
            article = db.get(Article, aid)
            article.current_snapshot_id = row['id']
            article.published_at = row['published_at']
    db.add(Brief(id='history', fingerprint='history', local_date='2025-01-01',
                 cutoff_at='2025-01-02T00:00:00Z', window_start='2025-01-01T00:00:00Z',
                 timezone='UTC', status='draft', data_mode='replay', generation_mode='extractive', preferences={}))
    db.flush()
    for position, sid in enumerate(data['historical_snapshot_ids']):
        snap = db.get(Snapshot, sid)
        db.add(BriefItem(brief_id='history', position=position, article_id=snap.article_id,
                        snapshot_id=sid, title=snap.title, summary='', source_name='synthetic',
                        source_url='https://example.invalid', topic='其他', evidence_quote=snap.text,
                        quote_start=0, quote_end=len(snap.text), verification='exact', relevance_reason='fixture'))
    db.commit()


def evaluate(path=DEFAULT_DATASET, k=5, *, dataset_loader=load_benchmark):
    if type(k) is not int or not 1 <= k <= 5:
        raise ValueError('k must be between 1 and 5 (lexical API limit)')
    data = dataset_loader(path)
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    results = []
    try:
        with Session(engine) as db:
            seed(db, data)
            for mode in ['lexical', 'hybrid']:
                details, recalls, ranks, abstentions = [], [], [], []
                valid_count = citation_count = leaks = 0
                for case in data['cases']:
                    settings = SimpleNamespace(retrieval_mode=mode, data_mode='replay',
                        embedding_provider='', embedding_model='', retrieval_lexical_k=50,
                        retrieval_vector_k=50, retrieval_final_k=5)
                    brief = 'history' if case['scope'] == 'historical' else None
                    # Independent fixture oracle, not the production scope builder.
                    scope = {d['id'] for d in data['documents']
                             if d.get('enabled', True) and d.get('data_mode', 'replay') == 'replay'
                             and (d['id'] in data['historical_snapshot_ids'] if brief
                                  else d.get('current', True))}
                    before = perf_counter()
                    result = search_research_evidence(db, settings, case['question'], brief)
                    elapsed = (perf_counter() - before) * 1000
                    citations = result['citations'][:k]
                    ranked = [c['snapshot_id'] for c in citations]
                    relevant = set(case['relevant_snapshot_ids'])
                    exact = sum(citation_matches_snapshot(db.get(Snapshot, c['snapshot_id']), c) for c in citations)
                    valid_count += exact
                    citation_count += len(citations)
                    leaks += len(set(ranked) - scope)
                    recall = recall_at_k(ranked, relevant, k) if relevant else None
                    rr = reciprocal_rank(ranked, relevant) if relevant else None
                    if relevant:
                        recalls.append(recall); ranks.append(rr)
                    else:
                        abstentions.append(not ranked)
                    details.append(dict(id=case['id'], tags=case['tags'], question=case['question'],
                        expected_snapshot_ids=sorted(relevant), returned_snapshot_ids=ranked,
                        citations=citations, recall_at_k=recall, reciprocal_rank=rr,
                        expected_no_answer=not relevant, retrieval_abstained=not ranked,
                        exact_quotes=exact, scope_violations=len(set(ranked)-scope), latency_ms=elapsed,
                        effective_mode=result['effective_mode'], degraded=result['degraded'],
                        degraded_reason=result['degraded_reason'], query_embedding=result['query_embedding'],
                        human_semantic_review={'status':'not_reviewed','support':None,'notes':None}))
                results.append(dict(requested_mode=mode, cases=len(details), answerable_cases=len(recalls),
                    no_answer_cases=len(abstentions), recall_at_k=sum(recalls)/len(recalls) if recalls else None,
                    mrr_at_k=sum(ranks)/len(ranks) if ranks else None,
                    no_answer_abstention_rate=sum(abstentions)/len(abstentions) if abstentions else None,
                    citation_count=citation_count, exact_quote_rate=valid_count/citation_count if citation_count else None,
                    scope_violations=leaks, details=details))
    finally:
        engine.dispose()
    return dict(dataset_version=data['version'], dataset_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        provenance=data['provenance'], label_origin=data['label_origin'], k=k, results=results,
        real_embedding={'status':'not_run','reason':'isolated SQLite; no provider configured'},
        human_semantic_review={'status':'not_reviewed','support_rate':None},
        limitations=[('Synthetic development fixture' if data['provenance']=='synthetic' else 'Public excerpt fixture with AI-authored questions and labels') + ', not held-out or representative real-world evaluation',
                     'Exact quotation does not establish semantic support or truth',
                     'Hybrid degraded to lexical; this is not a vector quality comparison',
                     'No generated answers: unsupported-answer rate is not measured',
                     'Latency is local observational timing, not a CI performance gate'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--k', type=int, default=5)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = evaluate(args.dataset, args.k)
    content = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(content, encoding='utf-8')
    else:
        print(content, end='')


if __name__ == '__main__':
    main()

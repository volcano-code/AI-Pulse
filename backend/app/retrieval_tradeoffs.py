"""Prespecified offline coverage-filter experiment; never changes production retrieval."""
import argparse
import hashlib
import json
from pathlib import Path
from .research_eval import evaluate, load_benchmark
from .retrieval import tokens
from .retrieval_eval import recall_at_k, reciprocal_rank

FIXTURES = Path(__file__).resolve().parents[1] / 'tests/fixtures'
PROTOCOL = FIXTURES / 'research_tradeoff_protocol_v1.json'
DEV = FIXTURES / 'research_eval_development_v3.json'
HOLDOUT = FIXTURES / 'research_eval_holdout_v3.json'


def score_filter(report, data, threshold, k=5):
    """All relevance labels remain evaluation-only, never inputs to the filter."""
    documents = {d['id']: d for d in data['documents']}
    details, recalls, ranks, refusals = [], [], [], []
    answerable_empty = 0
    for case in report['results'][0]['details']:  # actual lexical entry point
        query = set(tokens(case['question']))
        ranked, coverage = [], {}
        for sid in case['returned_snapshot_ids']:
            document = documents[sid]
            bag = set(tokens(document['title'] + ' ' + document['text']))
            fraction = len(query & bag) / len(query) if query else 0.0
            coverage[sid] = fraction
            if fraction >= threshold:
                ranked.append(sid)
        relevant = set(case['expected_snapshot_ids'])
        recall = recall_at_k(ranked, relevant, k) if relevant else None
        if relevant:
            recalls.append(recall)
            ranks.append(reciprocal_rank(ranked, relevant))
            answerable_empty += not ranked
        else:
            refusals.append(not ranked)
        details.append(dict(id=case['id'], question=case['question'], tags=case['tags'],
            expected_snapshot_ids=sorted(relevant), returned_snapshot_ids=ranked,
            baseline_snapshot_ids=case['returned_snapshot_ids'], token_coverage=coverage,
            recall_at_k=recall, retrieval_abstained=not ranked))
    return dict(threshold=threshold, answerable_cases=len(recalls), no_answer_cases=len(refusals),
        recall_at_k=sum(recalls)/len(recalls) if recalls else None,
        mrr_at_k=sum(ranks)/len(ranks) if ranks else None,
        no_answer_abstention_rate=sum(refusals)/len(refusals) if refusals else None,
        answerable_empty_count=answerable_empty, details=details)


def select_development(rows):
    baseline = next(r for r in rows if r['threshold'] == 0)
    if baseline['recall_at_k'] is None or baseline['no_answer_abstention_rate'] is None:
        raise ValueError('Development split requires answerable and no-answer cases')
    eligible = [r for r in rows if r['recall_at_k'] >= baseline['recall_at_k']]
    return max(eligible, key=lambda r: (r['no_answer_abstention_rate'], -r['threshold']))['threshold']


def run(dev_path=DEV, holdout_path=HOLDOUT):
    protocol = json.loads(PROTOCOL.read_text())
    dev, held = load_benchmark(dev_path), load_benchmark(holdout_path)
    if dev.get('split') != 'development' or held.get('split') != 'holdout':
        raise ValueError('Explicit development/holdout roles required')
    for field, records in [('id', 'cases'), ('question', 'cases'), ('id', 'documents'), ('article', 'documents'), ('text', 'documents')]:
        if {r[field] for r in dev[records]} & {r[field] for r in held[records]}:
            raise ValueError(f'Split overlap: {records}.{field}')
    # Selection never reads holdout retrieval or labels.
    development = evaluate(dev_path, protocol['k'])
    dev_rows = [score_filter(development, dev, t, protocol['k']) for t in protocol['thresholds']]
    selected = select_development(dev_rows)
    holdout = evaluate(holdout_path, protocol['k'])
    held_rows = [score_filter(holdout, held, t, protocol['k']) for t in protocol['thresholds']]
    return dict(protocol=protocol, protocol_sha256=hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        selected_threshold=selected, selection_data='development_only',
        splits={'development': {'sha256':development['dataset_sha256'], 'grid':dev_rows},
                'holdout': {'sha256':holdout['dataset_sha256'], 'grid':held_rows}},
        selected_holdout=next(r for r in held_rows if r['threshold']==selected),
        deployment_decision='not_applied; experiment only, review recall/abstention tradeoffs',
        provenance='synthetic; independent author holdout, not independent adjudication',
        real_embedding='not_run; no provider configured',
        observed_modes=[dict(requested_mode=row['requested_mode'],
            effective_modes=sorted({d['effective_mode'] for d in row['details']}),
            degraded_reasons=sorted({d['degraded_reason'] for d in row['details'] if d['degraded_reason']}))
            for row in development['results']],
        human_semantic_review='not_reviewed',
        limitations=['Retrieval refusal is not semantic support or factual correctness',
                     'No generated answers; unsupported-answer rate not measured',
                     'Small synthetic split; no real-world generalization claim',
                     'Holdout grid is diagnostic only and must not select the threshold',
                     'After this first evaluation the heldout data becomes a public regression set'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    text = json.dumps(run(), ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    else:
        print(text, end='')


if __name__ == '__main__':
    main()

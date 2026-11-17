"""Offline numeric-compatibility experiment. Not a production retrieval policy."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from .research_eval import evaluate, load_benchmark
from .retrieval_eval import recall_at_k, reciprocal_rank

FIXTURES = Path(__file__).resolve().parents[1] / 'tests/fixtures'
DEV = FIXTURES / 'research_eval_development_v4.json'
HOLDOUT = FIXTURES / 'research_eval_holdout_v4.json'
PROTOCOL = FIXTURES / 'research_numeric_protocol_v1.json'
POLICIES = ('baseline', 'reject_disjoint', 'require_all')


def numeric_atoms(text):
    # Deliberately narrow lexical identity, not numeric or semantic equivalence.
    return set(re.findall(r'[0-9]+(?:\.[0-9]+)*', text))


def accepts(question, document, policy):
    if policy not in POLICIES:
        raise ValueError('Unknown policy')
    query, saved = numeric_atoms(question), numeric_atoms(document)
    if policy == 'baseline':
        return True
    if policy == 'reject_disjoint':
        return not query or not saved or bool(query & saved)
    return query <= saved


def summarize(details):
    answerable = [d for d in details if d['expected_snapshot_ids']]
    no_answer = [d for d in details if not d['expected_snapshot_ids']]
    return dict(cases=len(details), answerable_cases=len(answerable), no_answer_cases=len(no_answer),
        recall_at_k=sum(d['recall_at_k'] for d in answerable)/len(answerable) if answerable else None,
        mrr_at_k=sum(d['reciprocal_rank'] for d in answerable)/len(answerable) if answerable else None,
        no_answer_abstention_rate=sum(d['retrieval_abstained'] for d in no_answer)/len(no_answer) if no_answer else None,
        answerable_empty_count=sum(d['retrieval_abstained'] for d in answerable))


def score(report, data, policy, k):
    documents = {d['id']: d for d in data['documents']}
    details = []
    for case in report['results'][0]['details']:
        ranked = []
        removed = []
        for sid in case['returned_snapshot_ids']:
            doc = documents[sid]
            if accepts(case['question'], doc['title'] + ' ' + doc['text'], policy):
                ranked.append(sid)
            else:
                removed.append(sid)
        relevant = set(case['expected_snapshot_ids'])
        recall = recall_at_k(ranked, relevant, k) if relevant else None
        details.append(dict(id=case['id'],question=case['question'],tags=case['tags'],
            expected_snapshot_ids=sorted(relevant), returned_snapshot_ids=ranked,
            removed_snapshot_ids=removed, query_numeric_atoms=sorted(numeric_atoms(case['question'])),
            recall_at_k=recall, reciprocal_rank=reciprocal_rank(ranked,relevant) if relevant else None,
            baseline_recall_at_k=case['recall_at_k'], retrieval_abstained=not ranked,
            recall_lost=relevant != set() and recall < case['recall_at_k'],
            no_answer_refusal_gained=not relevant and bool(case['returned_snapshot_ids']) and not ranked))
    tags = sorted({t for d in details for t in d['tags']})
    return dict(policy=policy, **summarize(details),
        answerable_cases_losing_recall=[d['id'] for d in details if d['recall_lost']],
        no_answer_cases_gaining_refusal=[d['id'] for d in details if d['no_answer_refusal_gained']],
        overlapping_tag_slices={t:summarize([d for d in details if t in d['tags']]) for t in tags}, details=details)


def select_development(rows):
    baseline = rows[0]
    if baseline['policy'] != 'baseline' or baseline['recall_at_k'] is None or baseline['no_answer_abstention_rate'] is None:
        raise ValueError('Baseline and both label classes required')
    eligible = [r for r in rows if r['recall_at_k'] >= baseline['recall_at_k']
                and not r['answerable_cases_losing_recall']]
    return max(eligible, key=lambda r:(r['no_answer_abstention_rate'], -POLICIES.index(r['policy'])))['policy']


def run(dev_path=DEV, holdout_path=HOLDOUT):
    protocol = json.loads(PROTOCOL.read_text())
    if protocol['candidates'] != list(POLICIES) or protocol['k'] != 5:
        raise ValueError('Protocol and implementation mismatch')
    dev, held = load_benchmark(dev_path), load_benchmark(holdout_path)
    if dev.get('split') != 'development' or held.get('split') != 'holdout':
        raise ValueError('Explicit split roles required')
    for records, field in [('documents','id'),('documents','article'),('documents','text'),('cases','id'),('cases','question')]:
        if {r[field] for r in dev[records]} & {r[field] for r in held[records]}:
            raise ValueError('Split overlap')
    development = evaluate(dev_path)
    dev_rows = [score(development, dev, p, 5) for p in POLICIES]
    selected = select_development(dev_rows)
    holdout = evaluate(holdout_path)
    held_rows = [score(holdout, held, p, 5) for p in POLICIES]
    return dict(protocol=protocol, protocol_sha256=hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        selected_policy=selected, selection_data='development_only',
        annotation_status='frozen_unadjudicated; scores reflect labels, not semantic gold',
        annotation_review_notes=[
            dict(split='development',case_ids=['n-dev-11'],issue='Free access may answer monthly price; no-answer label is ambiguous'),
            dict(split='holdout',case_ids=['h4_retention','h4_compare'],issue='Trial document also states formal retention; relevant sets may be non-exhaustive')],
        splits={'development':dict(sha256=development['dataset_sha256'],results=dev_rows),
                'holdout':dict(sha256=holdout['dataset_sha256'],results=held_rows)},
        selected_holdout=next(r for r in held_rows if r['policy']==selected),
        deployment_decision='not_applied; offline experiment only',
        real_embedding='not_run; no provider configured', human_semantic_review='not_reviewed',
        provenance='synthetic independent-author holdout; not independently adjudicated',
        limitations=['Numeric identity is not semantic entailment: negation, comparisons, units and dates require interpretation',
                     'Retrieval refusal is not a guarantee of supported generated answers',
                     'Tag slices overlap and must not be added as independent samples',
                     'No real-world or statistical generalization claim; no paid or real providers',
                     'Holdout diagnostics do not choose policy; after first disclosure these are regression data'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    output=json.dumps(run(),ensure_ascii=False,indent=2)+'\n'
    if args.output:
        args.output.write_text(output,encoding='utf-8')
    else:
        print(output,end='')


if __name__=='__main__':
    main()

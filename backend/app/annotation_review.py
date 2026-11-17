"""Versioned AI-provisional annotation overlays; exact checks are not human review."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from .research_eval import evaluate, load_benchmark

FIXTURES = Path(__file__).resolve().parents[1] / 'tests/fixtures'
DEFAULT_REVIEW = FIXTURES / 'annotation_review_v1.json'


def fail(message):
    raise ValueError(message)


def validate_review(data, raw_bytes, review):
    """Validate traceability and structure, never the truth of semantic judgments."""
    # The caller cannot hash frozen bytes but validate different in-memory text.
    canonical=lambda value: json.dumps(value,sort_keys=True,ensure_ascii=False)
    if canonical(data) != canonical(json.loads(raw_bytes)):
        fail('Dataset does not match source bytes')
    if review.get('schema_version') != 1 or review.get('status') != 'ai_provisional':
        fail('Only AI-provisional review schema 1 is supported')
    if review.get('reviewer_kind') != 'ai' or review.get('human_review_completed') is not False:
        fail('This workflow cannot attest human review')
    if not review.get('version') or not review.get('rubric_version'):
        fail('Review and rubric versions required')
    if review.get('source_sha256') != hashlib.sha256(raw_bytes).hexdigest():
        fail('Source dataset hash mismatch')
    if review.get('source_version') != data['version']:
        fail('Source version mismatch')
    cases={c['id']:c for c in data['cases']}
    docs={d['id']:d for d in data['documents']}
    decisions=review.get('decisions')
    if not isinstance(decisions,list) or not decisions:
        fail('Nonempty decisions required')
    seen=set()
    for decision in decisions:
        cid=decision.get('case_id')
        if cid not in cases or cid in seen:
            fail('Unknown or duplicate case ID')
        seen.add(cid); case=cases[cid]
        if decision.get('question') != case['question'] or decision.get('original_relevant_snapshot_ids') != case['relevant_snapshot_ids']:
            fail('Original annotation mismatch')
        for field in ['reason','falsification_condition','answer_scope','provisional_answer']:
            if not isinstance(decision.get(field),str) or not decision[field].strip():
                fail('Nonempty rationale, answer, scope and falsification required')
        relevant=decision.get('proposed_relevant_snapshot_ids')
        if not isinstance(relevant,list) or any(not isinstance(x,str) for x in relevant) or len(set(relevant))!=len(relevant):
            fail('Unique relevant IDs required')
        allowed={d['id'] for d in data['documents'] if d.get('enabled',True) and d.get('data_mode','replay')=='replay'
                 and (d['id'] in data['historical_snapshot_ids'] if case['scope']=='historical' else d.get('current',True))}
        if not set(relevant)<=allowed:
            fail('Proposed relevance escapes evidence scope')
        groups=decision.get('sufficient_evidence_sets')
        if not isinstance(groups,list) or bool(groups)!=bool(relevant):
            fail('Answerable annotations require sufficient evidence sets')
        frozen=[]
        for group in groups:
            if not isinstance(group,list) or not group or any(not isinstance(x,str) for x in group) or len(set(group))!=len(group) or not set(group)<=set(relevant):
                fail('Invalid sufficient evidence set')
            candidate=frozenset(group)
            if any(candidate<=old or old<=candidate for old in frozen):
                fail('Sufficient sets must be unique and minimal, not supersets')
            frozen.append(candidate)
        evidence=decision.get('evidence')
        if not isinstance(evidence,list):
            fail('Evidence list required')
        cited=set()
        for item in evidence:
            sid=item.get('snapshot_id'); start=item.get('start'); end=item.get('end'); quote=item.get('quote')
            if sid not in relevant or type(start) is not int or type(end) is not int:
                fail('Invalid evidence identity or offsets')
            text=docs[sid]['text']
            if not isinstance(quote,str) or not quote or not 0<=start<end<=len(text) or text[start:end]!=quote:
                fail('Quote does not match original snapshot')
            if item.get('role') not in ['direct','supporting_context']:
                fail('Explicit evidence role required')
            cited.add(sid)
        if cited != set(relevant):
            fail('Every proposed relevant document needs a traceable quotation')
    return True


def draft_review(data, raw_bytes, case_ids, version):
    """Start an explicitly incomplete review; validation will reject until filled."""
    cases={case['id']:case for case in data['cases']}
    if not case_ids or len(set(case_ids))!=len(case_ids) or not set(case_ids)<=cases.keys():
        fail('Unique known case IDs required')
    return dict(schema_version=1,source_version=data['version'],
        source_sha256=hashlib.sha256(raw_bytes).hexdigest(),version=version,
        rubric_version='evidence-support-v1',status='draft',reviewer_kind='ai',human_review_completed=False,
        decisions=[dict(case_id=cid,question=cases[cid]['question'],
            original_relevant_snapshot_ids=cases[cid]['relevant_snapshot_ids'][:],
            proposed_relevant_snapshot_ids=[],sufficient_evidence_sets=[],evidence=[],
            reason='',falsification_condition='',answer_scope='',provisional_answer='') for cid in case_ids])


def apply_review(data, raw_bytes, review):
    validate_review(data, raw_bytes, review)
    revised=deepcopy(data)
    changes={d['case_id']:d for d in review['decisions']}
    for case in revised['cases']:
        if case['id'] not in changes:
            continue
        decision=changes[case['id']]
        case['original_annotation']=deepcopy(case)
        case['relevant_snapshot_ids']=decision['proposed_relevant_snapshot_ids'][:]
        case['sufficient_evidence_sets']=deepcopy(decision['sufficient_evidence_sets'])
        case['rationale']=decision['reason']
        # Preserve original tags in original_annotation, avoid stale no-answer tags.
        case['tags']=[t for t in case['tags'] if t not in ['no_answer','answerable']]
        case['tags'].append('answerable' if case['relevant_snapshot_ids'] else 'no_answer')
    revised['version']=data['version']+'+'+review['version']
    revised['label_origin']='ai_provisional_overlay_not_human_adjudicated'
    return revised


def sufficient_set_covered(returned, groups):
    """Label-set coverage only: does not judge answer semantics or entailment."""
    return any(set(group)<=set(returned) for group in groups) if groups else None


def run(manifest_path=DEFAULT_REVIEW):
    manifest=json.loads(Path(manifest_path).read_text())
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('reviews'),list) or not manifest['reviews']:
        fail('Nonempty review manifest schema 1 required')
    sources_seen=set()
    comparisons=[]
    for entry in manifest['reviews']:
        # Source identifiers are registry keys, never arbitrary DB or network paths.
        sources={'development_v4':FIXTURES/'research_eval_development_v4.json',
                 'holdout_v4':FIXTURES/'research_eval_holdout_v4.json'}
        if entry['source'] in sources_seen:
            fail('Duplicate source review; publish one explicit version per source')
        sources_seen.add(entry['source'])
        if entry['source'] not in sources:
            fail('Unknown source registry key')
        path=sources[entry['source']]; raw=path.read_bytes(); data=load_benchmark(path)
        revised=apply_review(data,raw,entry)
        original_result=evaluate(path)
        with tempfile.TemporaryDirectory(prefix='pulse-annotation-') as directory:
            revised_path=Path(directory)/'provisional.json'
            revised_path.write_text(json.dumps(revised,ensure_ascii=False),encoding='utf-8')
            revised_result=evaluate(revised_path)
        rows=[]
        for before,after in zip(original_result['results'],revised_result['results']):
            changed=[]
            by_id={d['id']:d for d in after['details']}
            for decision in entry['decisions']:
                detail=by_id[decision['case_id']]
                changed.append(dict(case_id=decision['case_id'],
                    original_relevant_snapshot_ids=decision['original_relevant_snapshot_ids'],
                    provisional_relevant_snapshot_ids=decision['proposed_relevant_snapshot_ids'],
                    sufficient_evidence_sets=decision['sufficient_evidence_sets'],
                    sufficient_set_coverage_proxy=sufficient_set_covered(detail['returned_snapshot_ids'],decision['sufficient_evidence_sets']),
                    returned_snapshot_ids=detail['returned_snapshot_ids']))
            metrics=['answerable_cases','no_answer_cases','recall_at_k','mrr_at_k','no_answer_abstention_rate']
            rows.append(dict(requested_mode=before['requested_mode'],
                original_metrics={k:before[k] for k in metrics},provisional_metrics={k:after[k] for k in metrics},changes=changed))
        comparisons.append(dict(source=entry['source'],source_sha256=entry['source_sha256'],review_version=entry['version'],results=rows))
    return dict(schema_version=1,status='ai_provisional',human_review_completed=False,
        review_sha256=hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest(),comparisons=comparisons,
        limitations=['Validation proves provenance, offsets and set structure, not semantic correctness',
                     'AI-assisted proposals are not human or independent adjudication',
                     'Sufficient-set coverage is a label proxy, not generated-answer verification',
                     'Original datasets remain frozen; revised metrics are post-disclosure sensitivity analysis, not new holdout results',
                     'No production policy, provider call or user data change'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--review',type=Path,default=DEFAULT_REVIEW)
    args=parser.parse_args(); output=json.dumps(run(args.review),ensure_ascii=False,indent=2)+'\n'
    if args.output:
        args.output.write_text(output,encoding='utf-8')
    else:
        print(output,end='')


if __name__=='__main__':
    main()

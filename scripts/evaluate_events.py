"""Frozen synthetic sanity set for the deterministic event matcher.
Not a claim about production precision/recall on real news.
"""
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.events import similarity

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',default='evals/event_pairs_v1.json');p.add_argument('--threshold',type=float,default=.52);p.add_argument('--output',default='docs/validation-v0.4/event-eval.json');args=p.parse_args()
    pairs=json.loads((ROOT/args.input).read_text());tp=fp=fn=tn=0;rows=[]
    for row in pairs:
        score=similarity(row['a'],row['b']);pred=score>=args.threshold;truth=bool(row['same'])
        tp+=pred and truth;fp+=pred and not truth;fn+=(not pred) and truth;tn+=(not pred) and not truth
        rows.append(row|{'score':round(score,4),'predicted_same':pred})
    precision=tp/max(1,tp+fp);recall=tp/max(1,tp+fn);f1=2*precision*recall/max(1e-9,precision+recall)
    result={'dataset':'synthetic bilingual sanity set','pairs':len(pairs),'threshold':args.threshold,'tp':tp,'fp':fp,'fn':fn,'tn':tn,'precision':round(precision,4),'recall':round(recall,4),'f1':round(f1,4),'rows':rows,'warning':'Synthetic regression fixture only; real-news labeled evaluation is still required.'}
    out=ROOT/args.output;out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2,ensure_ascii=False));print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False))
    return 0 if precision>=.8 and recall>=.8 else 1
if __name__=='__main__':raise SystemExit(main())

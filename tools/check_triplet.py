import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'scripts'))
from viewer.data import load_samples,group_value_triplets
rows=load_samples(root/'data/samples/value_qa/verified.marked_merged.jsonl');assert len(rows)==3;groups=group_value_triplets(rows);assert len(groups)==1;assert {r.value_level for r in rows}=={'high','medium','low'}
print('approved minimal triplet: passed')

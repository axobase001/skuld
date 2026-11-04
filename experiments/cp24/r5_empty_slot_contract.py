"""Pre-code RED from real R4 #439 snapshot; zero provider calls."""
import json, sys
from pathlib import Path
sys.path.insert(0,'/content/cp24_review')
from cp24_extract_results import unpack_task
from skuld.loop.tool_subject import precondition_status
rows=[json.loads(x) for x in Path('/content/cp24_dev_r4/calls.jsonl').read_text().splitlines() if x]
task=unpack_task(rows[438]['request'])
table=task['business_state']['current_inventory']
row=next(dict(zip(table['columns'],r)) for r in table['rows'] if r[0]=='AI-LOUNGE:DRINKS:A3')
row['quantity_status']='OBSERVED'
assert row['product_id'] is None and row['quantity']==0 and row['price_status']=='UNBOUND' and row['price_eur'] is None
response=json.loads(rows[438]['response']['choices'][0]['message']['content'])
action=next(x for x in response['actions'] if x['tool']=='restock_machine' and x['arguments'].get('slot')==row['slot_id'])
assert action['assumption_refs']['price_eur'] is None
bad,unknown=precondition_status(action['assumption_refs'],row)
assert not bad and not unknown,(bad,unknown)
unknown_row={**row,'price_status':'UNKNOWN'}
_,unknown=precondition_status({'price_eur':None},unknown_row)
assert 'price_eur' in unknown,unknown
priced={**row,'price_status':'OBSERVED','price_eur':1.8}
bad,_=precondition_status({'price_eur':None},priced)
assert 'price_eur' in bad,bad
_,unknown=precondition_status({'price_eur':None},None)
assert 'price_eur' in unknown,unknown
for inconsistent in ({**row,'product_id':'cola-500ml'},{**row,'quantity_status':'UNKNOWN'}):
    _,unknown=precondition_status({'price_eur':None},inconsistent)
    assert 'price_eur' in unknown,unknown
print('R5_EMPTY_SLOT_CONTRACT_GREEN')

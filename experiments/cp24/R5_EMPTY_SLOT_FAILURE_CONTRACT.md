# CP24 R5 pre-code failure contract

Source: R4 ledger #439/#440, B-RB seed 2, day 6. Both returned real deepseek-flash responses. The public inventory shows AI-LOUNGE:DRINKS:A3 product_id=null, quantity=0, price_status=UNBOUND, price_eur=null. The model asserted price_eur=null before restock; host rejected UNKNOWN, so dependent set_price could not run.

Required behavior: a source-observed empty slot with price_status UNBOUND and price_eur null may satisfy an explicit null price precondition. A truly UNKNOWN price may not. An observed numeric price must still reject an incorrect null. Missing slot must stay unknown. The same original action response must reach actual public restock and its follow-up set_price through Runtime, or report the next specific blocker. Never substitute an answer, weaken transactional authorization, or count an unexecuted action as progress.

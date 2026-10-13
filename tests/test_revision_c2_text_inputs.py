"""Ensure parser handles values/order, not record identity or gold labels."""
import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('c2',Path(__file__).parents[1]/'scripts/revision_20260908/c2_fair_input_evaluation.py')
c2=importlib.util.module_from_spec(spec)
spec.loader.exec_module(c2)

def test_ticket_reversed_pairs_and_counterfactual():
    text='authority/action times 08:20/09:10, state/ticket devices EQ-9412/EQ-9412, planned/required observation 45/20 minutes.'
    fields=c2.parse_instruction(text,c2.TICKET)
    assert fields['permit_minute']==500
    assert fields['start_minute']==550
    assert c2.specified_rule(fields,c2.TICKET)=='compliant'
    mismatched=text.replace('EQ-9412/EQ-9412','EQ-9413/EQ-9412')
    assert c2.specified_rule(c2.parse_instruction(mismatched,c2.TICKET),c2.TICKET)=='non_compliant'

def test_route_reversed_pair_priority():
    task='dispatcher_intent_tool_call'
    fields=c2.parse_instruction('reserve-exposure 60.0-80.0, floor-voltage 0.950-0.960, limit-loading 100.0-75.0.',task)
    assert fields['redispatch_reserve']==60
    assert fields['contingency_exposure']==80
    assert c2.specified_rule(fields,task)=='security_check_and_redispatch'

def test_missing_fields_are_not_fabricated():
    assert c2.parse_instruction('No measurements available.',c2.TICKET)=={}

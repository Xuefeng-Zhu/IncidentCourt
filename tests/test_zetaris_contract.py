"""Official-envelope fixtures prove parsing only, not live Zetaris access."""
import unittest
from incidentcourt.zetaris_contract import decode_rows, normalize_row, ZetarisContractError


class ZetarisContractTests(unittest.TestCase):
    def test_official_headers_data_envelope_and_exponential_numbers(self):
        rows=decode_rows({'headers':['service','baseline','current','baseline_n','current_n'],
                          'data':[['service-a','1.2E-3','2.4e-2','1.0E2','90']]})
        self.assertEqual(rows,[{'service':'service-a','baseline':.0012,'current':.024,'baseline_n':100,'current_n':90}])
        self.assertEqual(decode_rows({'headers':['connected'],'data':[['1']]}),[{'connected':1}])

    def test_ids_and_real_nulls_survive(self):
        rows=decode_rows({'headers':['id','span_id','trace_id','duration_ms','status','value'],
                          'data':[['IC-001-t-0','000001','001234','1.2E3',None,None]]})
        self.assertEqual(rows[0]['span_id'],'000001')
        self.assertEqual(rows[0]['trace_id'],'001234')
        self.assertEqual(rows[0]['duration_ms'],1200)
        self.assertIsNone(rows[0]['status'])
        self.assertIsNone(rows[0]['value'])
        with self.assertRaises(ZetarisContractError):
            normalize_row({'span_id':123})

    def test_explicit_empty_is_valid_but_missing_or_error_is_not_empty(self):
        self.assertEqual(decode_rows({'headers':['id'],'data':[]}),[])
        for payload in ({},{'headers':['id']},{'headers':['id'],'data':[], 'error':'fixture-sensitive-error'},
                        {'headers':['id'],'data':[], 'success':False}):
            with self.subTest(payload=payload),self.assertRaises(ZetarisContractError) as error:
                decode_rows(payload)
            self.assertNotIn('fixture-sensitive-error',str(error.exception))

    def test_malformed_duplicate_nested_oversized_and_nonfinite_values_fail(self):
        for payload in (
            {'headers':['id','ID'],'data':[['a','b']]},
            {'headers':['id'],'data':[['a','b']]},
            {'headers':['id'],'data':[{'id':'a'}]},
            {'headers':['service'],'data':[[{'nested':'value'}]]},
            {'headers':['connected'],'data':[['1']]*1001},
            {'headers':['value'],'data':[['NaN']]},
            {'headers':['value'],'data':[['Infinity']]},
            {'headers':['baseline_n'],'data':[['2.5']]},
            {'headers':['baseline_n'],'data':[['1e1000000000']]},
            {'headers':['current'],'data':[['NULL']]},
        ):
            with self.subTest(payload=str(payload)[:100]),self.assertRaises(ZetarisContractError):
                decode_rows(payload)


if __name__=='__main__':unittest.main()

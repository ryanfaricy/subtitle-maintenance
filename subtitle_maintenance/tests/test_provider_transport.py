import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from subtitle_maintenance.providers import (Provider,ProviderTransportError,
    read_helper_response,legacy_transport_cooldown,provider_error_label)

class ProviderTransportTests(unittest.TestCase):
    def test_reader_partial_timeout_eof_and_bad_json(self):
        for payload,expected in [(b'{"data": []}\n',None),(b'{','response_timeout'),
                                 (b'not-json\n','invalid_json'),(b'[]\n','invalid_response_shape')]:
            read,write=os.pipe()
            with os.fdopen(read,'rb',buffering=0) as source:
                try:
                    os.write(write,payload)
                    process=SimpleNamespace(stdout=source)
                    if expected:
                        with self.assertRaisesRegex(ProviderTransportError,expected):read_helper_response(process,0.01)
                    else:self.assertEqual(read_helper_response(process,0.01),{'data':[]})
                finally:os.close(write)
        read,write=os.pipe();os.close(write)
        with os.fdopen(read,'rb',buffering=0) as source:
            with self.assertRaisesRegex(ProviderTransportError,'helper_eof'):
                read_helper_response(SimpleNamespace(stdout=source),0.01)

    def test_search_retries_once(self):
        with tempfile.TemporaryDirectory() as d:
            p=Provider({},Path(d))
            with patch.object(p,'exchange',side_effect=[ProviderTransportError('helper_eof'),{'data':[]}]) as exchange,patch.object(p,'close') as close:
                self.assertEqual(p.request({'action':'search'}),{'data':[]})
                self.assertEqual(exchange.call_count,2);close.assert_called_once()

    def test_download_never_replayed_and_diagnostics_saved(self):
        with tempfile.TemporaryDirectory() as d:
            p=Provider({},Path(d))
            with patch.object(p,'exchange',side_effect=BrokenPipeError('secret not to log')) as exchange,patch.object(p,'close'):
                with self.assertRaisesRegex(RuntimeError,'BrokenPipeError'):p.request({'action':'download'})
                self.assertEqual(exchange.call_count,1)
            saved=json.loads((Path(d)/'provider-cooldown.json').read_text())
            self.assertNotIn('secret',json.dumps(saved))
            self.assertFalse(legacy_transport_cooldown(saved))
            with patch.object(p,'exchange') as exchange:
                with self.assertRaisesRegex(RuntimeError,'retry after'):p.request({'action':'search'})
                exchange.assert_not_called()

    def test_search_retry_bounded_and_legacy_narrow(self):
        with tempfile.TemporaryDirectory() as d:
            p=Provider({},Path(d))
            with patch.object(p,'exchange',side_effect=ProviderTransportError('response_timeout')) as exchange,patch.object(p,'close'):
                with self.assertRaisesRegex(RuntimeError,'response_timeout'):p.request({'action':'search'})
                self.assertEqual(exchange.call_count,2)
        self.assertTrue(legacy_transport_cooldown({'error':{'error':'Provider transport failure'}}))
        self.assertFalse(legacy_transport_cooldown({'error':{'error':'HTTPError','status':429}}))
        self.assertNotIn('None None',provider_error_label({'error':'Provider transport failure'}))

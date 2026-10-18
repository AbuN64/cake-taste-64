import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cake_taste import TasteError
from cake_taste.repro.cli import run


class ReproDifferencesTests(unittest.TestCase):
    def test_public_probe_fails_after_recording_difference_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / 'request.json'
            request.write_text('{}')
            story = dict(question='Does reset run?', function='reset', owner_address='0x80001000',
                         decomp_path='reset.s', match_state='unknown')
            result = dict(run_id='frozen-run', receipt=str(root / 'receipt.json'),
                          probe=dict(function='reset', checked=4, differences=1))
            with patch('cake_taste.repro.cli.probe_story', return_value=story), \
                 patch('cake_taste.repro.cli.bounded_probe', return_value=result), \
                 patch('cake_taste.repro.narration.append_event') as append:
                with self.assertRaisesRegex(TasteError, '1 differences; run: frozen-run; receipt:'):
                    run(object(), ('probe', '--kind', 'call', '--input', str(request)))
                self.assertEqual(append.call_args.args[2], 'failed')
                self.assertEqual(append.call_args.kwargs['payload']['run_id'], 'frozen-run')
                self.assertEqual(append.call_args.args[4], str(root / 'probe.json'))

    def test_public_probe_still_returns_passing_evidence(self):
        with patch('cake_taste.repro.cli.probe_story', return_value=dict(
            question='Read?', function='read', owner_address='0', decomp_path='read.s', match_state='unknown'
        )), patch('cake_taste.repro.narration.append_event'), patch(
            'cake_taste.repro.cli.bounded_probe', return_value=dict(
                run_id='pass', receipt='/receipt.json', probe=dict(checked=1, differences=0)
            )
        ):
            self.assertEqual(run(object(), ('probe', '--kind', 'call', '--input', 'request.json'))['run_id'], 'pass')

import unittest
from unittest.mock import patch

from core import aelaunch, proc


class AeProcessLookupTest(unittest.TestCase):
    def test_checking_for_ae_uses_native_process_list_without_tasklist(self):
        with patch.object(proc, "list_ae_processes", return_value=[{"pid": 1234}]), \
                patch.object(aelaunch.subprocess, "run") as command:
            self.assertEqual(aelaunch.ae_pids(), [1234])
            self.assertTrue(aelaunch.ae_running())
            command.assert_not_called()

"""运行本轮13项接口/产物验收，保存结构化结果与退出码。"""
from common import *
from datetime import datetime, timezone
import sys
import unittest

if __name__ == '__main__':
    suite = unittest.defaultTestLoader.discover(str(REPORT / 'tests'), pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    write_json(REPORT / 'ACCEPTANCE_RESULTS.json', {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'groups_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'passed': result.wasSuccessful(), 'failure_details': [(str(t), m) for t, m in result.failures + result.errors],
        'scope': '13 user acceptance groups; CPU only, no historical suite or driving'})
    sys.exit(0 if result.wasSuccessful() else 1)

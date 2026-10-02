"""把 pytest JUnit XML 转成精简、确定性的 JSON 摘要。"""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree


def build_pytest_summary(junit_xml: Path, output_path: Path) -> dict[str, object]:
    root = ElementTree.parse(junit_xml).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", 0)) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", 0)) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", 0)) for suite in suites)
    duration = sum(float(suite.attrib.get("time", 0.0)) for suite in suites)
    summary: dict[str, object] = {
        "result_schema_version": "driveclarify.pytest_summary.v0.1",
        "status": "PASS" if failures == 0 and errors == 0 else "FAIL",
        "tests": tests,
        "passed": tests - failures - errors - skipped,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "duration_s": round(duration, 6),
        "command": "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/offline_v0 --junitxml=reports/offline_v0/results/pytest.xml",
        "cpu_only": True,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary

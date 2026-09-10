"""v19.20: THERAPEUT-Feld im prompts.log-Header."""
from __future__ import annotations
import logging
from unittest.mock import patch


class _Job:
    therapeut_id = "c.saur"


class TestTherapeutImLog:
    def test_prompt_und_output_header(self, caplog):
        from app.api import jobs as J
        records = []
        handler = logging.Handler()
        handler.emit = lambda r: records.append(r.getMessage())
        J._prompt_logger.addHandler(handler)
        try:
            with patch.object(J.job_queue, "get_job", lambda jid: _Job()):
                J._log_prompt("abc123", "anamnese", "anamnese", "SYS", "USER")
                J._log_output("abc123", "anamnese", "anamnese", "OUT", {"tokens_hit_cap": False})
        finally:
            J._prompt_logger.removeHandler(handler)
        assert any("JOB: abc123  |  WORKFLOW: anamnese  |  CALL: anamnese  |  THERAPEUT: c.saur" in r for r in records)
        assert any("THERAPEUT: c.saur  (OUTPUT)" in r for r in records)

    def test_unbekannter_job_strich(self):
        from app.api import jobs as J
        with patch.object(J.job_queue, "get_job", lambda jid: None):
            assert J._therapeut_for_log("nope") == "-"

    def test_alte_header_bleiben_parsebar(self):
        import re
        pat = re.compile(r"JOB: (\w+)\s+\|\s+WORKFLOW: (\S+)\s+\|\s+CALL: (\S+)(?:\s+\|\s+THERAPEUT: (\S+))?(.*)")
        old = "JOB: a1  |  WORKFLOW: x  |  CALL: x  (OUTPUT)  [words=3]"
        new = "JOB: a1  |  WORKFLOW: x  |  CALL: x  |  THERAPEUT: t.p  (OUTPUT)  [words=3]"
        assert pat.search(old).group(4) is None and "(OUTPUT)" in pat.search(old).group(5)
        assert pat.search(new).group(4) == "t.p" and "(OUTPUT)" in pat.search(new).group(5)

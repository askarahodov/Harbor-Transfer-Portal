#!/usr/bin/env python3
"""Contract tests for the scope-selected CI jobs and the `quality-gate` gate.

The three regressions behind #566/#568/#569 were all the same class of bug: a
job was selected by a scope condition that `quality-gate` did not account for
(or vice versa), so a failing required job could be reported as success, or a
skipped job could be treated as an unexpected result.

These tests parse the real `.github/workflows/ci.yml` and `tools/ci_scope.py`
and enforce a single contract:

A. Every scope-gated job's `if:` areas are reflected in `quality-gate`'s
   `*_REQUIRED` env (including compound `a == 'true' || b == 'true'`).
B. Every `*_REQUIRED`/`*_RESULT` pair is iterated in the gate's `for area in`.
C. `quality-gate.needs` lists every job whose `.result` it reads.
D. Every `ci_scope._AREA_NAMES` area either gates a real job or is explicitly
   allowlisted as informational/future.

Only the standard library plus PyYAML is required; no network access.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tools import ci_scope


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW_PATH = _REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"

_QUALITY_GATE_JOB = "quality-gate"
_SCOPE_JOB = "scope"

# `SCOPE_RESULT` is intentionally read by quality-gate but has no
# `SCOPE_REQUIRED`/loop entry: the scope job must always succeed, so it is
# checked up front (`[[ "$SCOPE_RESULT" == "success" ]]`) instead of via the
# per-area `for area in ...` loop.
_GATE_RESULTS_OUTSIDE_AREA_LOOP = frozenset({"SCOPE"})

# scope area name -> workflow job key. The default mapping is identity, so only
# the non-obvious wiring is listed here. Adding a scope-gated job that does not
# follow the identity mapping must add an entry here; the test fails otherwise.
_JOB_TO_SCOPE_AREAS = {
    # Windows launcher/offline lifecycle contracts are gated on `compose`.
    "windows-launcher": ("compose",),
    "windows-offline-lifecycle": ("compose",),
    # The `integration` job carries isolated acceptance (gated on `integration`)
    # and Nexus legacy Docker v2 E2E (gated on `nexus_e2e`); its job-level `if`
    # runs for either area, so the gate must require it in both cases (#569).
    "integration": ("integration", "nexus_e2e"),
}

# Scope areas that are informational/future: they currently do not gate a job.
# Every area here needs a justification, and the drift guard below requires it to
# be either listed here or wired to a job.
_INFORMATIONAL_SCOPE_AREAS: dict[str, str] = {}

_SCOPE_OUTPUT_RE = re.compile(
    r"needs\.(?:scope|['\"]scope['\"])\.outputs\.([A-Za-z0-9_-]+)"
)
_NEEDS_RESULT_RE = re.compile(
    r"needs(?:\.([A-Za-z0-9_-]+)|\[\s*['\"]([A-Za-z0-9_-]+)['\"]\s*\])\.result"
)
_FOR_AREA_RE = re.compile(r"for\s+area\s+in\s+([A-Za-z0-9_ ]+?)(?:;|\bdo\b)")


def _load_workflow(path: Path = _WORKFLOW_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _flatten_xml(value: object) -> str:
    """Разворачивает `if:`/`needs` в один текст, снимая обёртку `${{ }}`."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("${{") and text.endswith("}}"):
            text = text[3:-2].strip()
        return text
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_xml(item) for item in value)
    return str(value)


def _job_needs(job: dict) -> list[str]:
    needs = job.get("needs")
    if needs is None:
        return []
    if isinstance(needs, str):
        return [needs]
    return [str(item) for item in needs]


def _scope_areas_used_in(expression: str) -> set[str]:
    return set(_SCOPE_OUTPUT_RE.findall(expression))


def _job_names_whose_result_is_read(expression: str) -> set[str]:
    names: set[str] = set()
    for plain, quoted in _NEEDS_RESULT_RE.findall(expression):
        names.add(plain or quoted)
    return names


def _gate_env(job: dict) -> dict[str, str]:
    env: dict[str, str] = {}
    for step in job.get("steps", []) or []:
        for key, value in (step.get("env") or {}).items():
            env[key] = "" if value is None else str(value)
    return env


def _gate_run_script(job: dict) -> str:
    return "\n".join(
        str(step.get("run", "")) for step in job.get("steps", []) or []
    )


def _required_areas(env: dict[str, str]) -> dict[str, set[str]]:
    """`*_REQUIRED` key -> scope areas referenced by its expression."""
    required: dict[str, set[str]] = {}
    for key, value in env.items():
        if key.endswith("_REQUIRED"):
            required[key] = _scope_areas_used_in(_flatten_xml(value))
    return required


def _iterated_areas(run_script: str) -> set[str]:
    match = _FOR_AREA_RE.search(run_script)
    if not match:
        return set()
    return set(match.group(1).split())


def _default_areas_for_job(job_key: str) -> tuple[str, ...]:
    return _JOB_TO_SCOPE_AREAS.get(job_key, (job_key,))


def _scope_gated_jobs(workflow: dict) -> dict[str, set[str]]:
    """Jobs (other than scope/quality-gate) whose `if:` reads scope outputs."""
    gated: dict[str, set[str]] = {}
    for job_key, job in workflow["jobs"].items():
        if job_key in (_SCOPE_JOB, _QUALITY_GATE_JOB):
            continue
        areas = _scope_areas_used_in(_flatten_xml(job.get("if")))
        if areas:
            gated[job_key] = areas
    return gated


def _run_contract_suite(workflow: dict) -> tuple[list[str], dict[str, int]]:
    """Pure check of the contract, used both by tests and by the mutation test.

    Returns (failures, stats) so callers can assert the checker is wired to real
    data instead of trivially passing on an empty workflow.
    """
    failures: list[str] = []
    jobs = workflow.get("jobs", {})
    gate = jobs.get(_QUALITY_GATE_JOB)
    if gate is None:
        return ["quality-gate job is missing from ci.yml"], {"gated": 0, "areas": 0}

    env = _gate_env(gate)
    required = _required_areas(env)
    required_areas: set[str] = set()
    for value in required.values():
        required_areas |= value

    gated = _scope_gated_jobs(workflow)

    # A. job `if:` areas are covered by the gate's REQUIRED areas.
    for job_key, job_areas in sorted(gated.items()):
        expected = set(_default_areas_for_job(job_key))
        if job_areas != expected:
            failures.append(
                f"job '{job_key}' gates on {sorted(job_areas)} but the test's "
                f"job->scope mapping expects {sorted(expected)}; update "
                f"_JOB_TO_SCOPE_AREAS explicitly"
            )
        missing = job_areas - required_areas
        if missing:
            failures.append(
                f"job '{job_key}' requires scope {sorted(missing)} but "
                f"quality-gate has no *_REQUIRED for it"
            )
    # Every area the gate requires must actually gate some job (inverse of A).
    ungated_required = required_areas - {a for areas in gated.values() for a in areas}
    if ungated_required:
        failures.append(
            f"quality-gate requires scope {sorted(ungated_required)} but no job "
            f"is gated on it"
        )

    # B. each *_REQUIRED/*_RESULT is iterated in `for area in ...`.
    iterated = _iterated_areas(_gate_run_script(gate))
    required_keys = {k[: -len("_REQUIRED")] for k in required}
    result_keys = {
        k[: -len("_RESULT")]
        for k in env
        if k.endswith("_RESULT") and k[: -len("_RESULT")] not in _GATE_RESULTS_OUTSIDE_AREA_LOOP
    }
    for area in sorted(required_keys ^ result_keys):
        failures.append(
            f"quality-gate has {area}_REQUIRED xor {area}_RESULT; the pair must "
            "be defined together"
        )
    for area in sorted(required_keys | result_keys):
        if area not in iterated:
            failures.append(
                f"quality-gate defines {area}_REQUIRED/{area}_RESULT but does not "
                f"iterate it in `for area in ...` (checked: {sorted(iterated)})"
            )
    for area in sorted(iterated):
        if area not in required_keys or area not in result_keys:
            failures.append(
                f"quality-gate iterates '{area}' in `for area in ...` but has no "
                f"matching {area}_REQUIRED/{area}_RESULT env"
            )
    for area in sorted(_GATE_RESULTS_OUTSIDE_AREA_LOOP):
        if f"{area}_RESULT" not in env:
            failures.append(
                f"quality-gate is missing the out-of-loop {area}_RESULT env"
            )
        if _gate_run_script(gate).find(f"{area}_RESULT") == -1:
            failures.append(
                f"quality-gate defines {area}_RESULT but never reads it in run:"
            )

    # C. quality-gate.needs covers every job whose .result it reads.
    gate_needs = set(_job_needs(gate))
    gate_text = _flatten_xml(gate.get("if")) + " " + " ".join(
        _flatten_xml(str(job)) for job in gate.get("steps", []) or []
    )
    read_jobs = _job_names_whose_result_is_read(gate_text)
    missing_needs = read_jobs - gate_needs
    if missing_needs:
        failures.append(
            f"quality-gate reads needs.<job>.result for {sorted(missing_needs)} "
            f"but does not list them in needs"
        )
    dangling_needs = gate_needs - set(jobs)
    if dangling_needs:
        failures.append(
            f"quality-gate.needs references unknown jobs {sorted(dangling_needs)}"
        )

    # D. every scope area gates a job or is explicitly informational.
    gated_areas = {a for areas in gated.values() for a in areas}
    area_names = set(ci_scope._AREA_NAMES)
    unknown_areas = area_names - gated_areas - set(_INFORMATIONAL_SCOPE_AREAS)
    if unknown_areas:
        failures.append(
            f"ci_scope areas {sorted(unknown_areas)} gate no job and are not in "
            f"_INFORMATIONAL_SCOPE_AREAS"
        )
    stale_informational = set(_INFORMATIONAL_SCOPE_AREAS) & gated_areas
    if stale_informational:
        failures.append(
            f"ci_scope areas {sorted(stale_informational)} are allowlisted as "
            f"informational but now gate a job; remove them from the allowlist"
        )
    for area, reason in _INFORMATIONAL_SCOPE_AREAS.items():
        if not reason.strip():
            failures.append(f"informational scope area '{area}' needs a reason")
    unknown_gated = gated_areas - area_names
    if unknown_gated:
        failures.append(
            f"jobs gate on scope areas {sorted(unknown_gated)} missing from "
            f"ci_scope._AREA_NAMES"
        )

    stats = {"gated": len(gated), "areas": len(area_names)}
    return failures, stats


class CiWorkflowContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = _load_workflow()
        cls.jobs = cls.workflow["jobs"]
        cls.gate = cls.jobs[_QUALITY_GATE_JOB]
        cls.gate_env = _gate_env(cls.gate)
        cls.required = _required_areas(cls.gate_env)
        cls.required_areas = {
            area for areas in cls.required.values() for area in areas
        }
        cls.gated = _scope_gated_jobs(cls.workflow)

    def test_workflow_and_scope_are_parsed(self) -> None:
        self.assertIn(_SCOPE_JOB, self.jobs)
        self.assertIn(_QUALITY_GATE_JOB, self.jobs)
        self.assertTrue(self.required, "quality-gate has no *_REQUIRED env")
        self.assertTrue(self.gated, "no scope-gated jobs found in ci.yml")
        # Guard against a test that silently passes because it parsed nothing.
        self.assertGreaterEqual(len(ci_scope._AREA_NAMES), 8)

    def test_scope_outputs_exist(self) -> None:
        outputs = set(self.jobs[_SCOPE_JOB].get("outputs", {}))
        used: set[str] = set()
        for job in self.jobs.values():
            used |= _scope_areas_used_in(_flatten_xml(job.get("if")))
        used |= self.required_areas
        for area in sorted(used):
            with self.subTest(area=area):
                self.assertIn(
                    area,
                    outputs,
                    f"scope area '{area}' is read but not a scope job output",
                )

    def test_gated_jobs_follow_explicit_scope_mapping(self) -> None:
        for job_key, job_areas in sorted(self.gated.items()):
            expected = set(_default_areas_for_job(job_key))
            with self.subTest(job=job_key):
                self.assertEqual(
                    job_areas,
                    expected,
                    f"job '{job_key}' gates on {sorted(job_areas)}; the explicit "
                    f"job->scope mapping in this test expects {sorted(expected)}. "
                    "Update _JOB_TO_SCOPE_AREAS with a comment explaining the "
                    "wiring.",
                )

    def test_each_gated_job_is_required_by_quality_gate(self) -> None:
        for job_key, job_areas in sorted(self.gated.items()):
            for area in sorted(job_areas):
                with self.subTest(job=job_key, area=area):
                    self.assertIn(
                        area,
                        self.required_areas,
                        f"job '{job_key}' requires scope '{area}' but "
                        f"quality-gate has no *_REQUIRED for it",
                    )

    def test_quality_gate_requires_only_gated_areas(self) -> None:
        gated_areas = {a for areas in self.gated.values() for a in areas}
        for area in sorted(self.required_areas):
            with self.subTest(area=area):
                self.assertIn(
                    area,
                    gated_areas,
                    f"quality-gate has *_REQUIRED for '{area}' but no job is "
                    "gated on it",
                )

    def test_required_and_result_keys_match(self) -> None:
        required_keys = {k[: -len("_REQUIRED")] for k in self.required}
        result_keys = {
            k[: -len("_RESULT")]
            for k in self.gate_env
            if k.endswith("_RESULT")
            and k[: -len("_RESULT")] not in _GATE_RESULTS_OUTSIDE_AREA_LOOP
        }
        with self.subTest(direction="required-without-result"):
            self.assertEqual(
                required_keys - result_keys,
                set(),
                "quality-gate defines *_REQUIRED without matching *_RESULT",
            )
        with self.subTest(direction="result-without-required"):
            self.assertEqual(
                result_keys - required_keys,
                set(),
                "quality-gate defines *_RESULT without matching *_REQUIRED",
            )

    def test_every_key_is_iterated_in_for_area_loop(self) -> None:
        iterated = _iterated_areas(_gate_run_script(self.gate))
        self.assertTrue(iterated, "quality-gate has no `for area in ...` loop")
        required_keys = {k[: -len("_REQUIRED")] for k in self.required}
        result_keys = {
            k[: -len("_RESULT")]
            for k in self.gate_env
            if k.endswith("_RESULT")
            and k[: -len("_RESULT")] not in _GATE_RESULTS_OUTSIDE_AREA_LOOP
        }
        for area in sorted(required_keys | result_keys):
            with self.subTest(area=area):
                self.assertIn(
                    area,
                    iterated,
                    f"quality-gate defines {area}_REQUIRED/{area}_RESULT but "
                    "does not check it in `for area in ...`",
                )
        for area in sorted(iterated):
            with self.subTest(loop_entry=area):
                self.assertIn(
                    area,
                    required_keys & result_keys,
                    f"quality-gate iterates '{area}' but has no matching env",
                )

    def test_quality_gate_needs_every_job_it_reads(self) -> None:
        gate_needs = set(_job_needs(self.gate))
        gate_text = _flatten_xml(self.gate.get("if")) + " " + " ".join(
            _flatten_xml(str(step)) for step in self.gate.get("steps", []) or []
        )
        read_jobs = _job_names_whose_result_is_read(gate_text)
        self.assertTrue(read_jobs, "quality-gate reads no needs.<job>.result")
        for job_key in sorted(read_jobs):
            with self.subTest(job=job_key):
                self.assertIn(
                    job_key,
                    gate_needs,
                    f"quality-gate reads needs.{job_key}.result but "
                    "does not list it in needs",
                )
        for job_key in sorted(gate_needs):
            with self.subTest(needs=job_key):
                self.assertIn(job_key, self.jobs, "needs references unknown job")

    def test_every_scope_area_gates_a_job_or_is_allowlisted(self) -> None:
        gated_areas = {a for areas in self.gated.values() for a in areas}
        for area in ci_scope._AREA_NAMES:
            with self.subTest(area=area):
                self.assertTrue(
                    area in gated_areas or area in _INFORMATIONAL_SCOPE_AREAS,
                    f"ci_scope area '{area}' gates no job and is not in the "
                    "informational/future allowlist",
                )
                self.assertFalse(
                    area in gated_areas and area in _INFORMATIONAL_SCOPE_AREAS,
                    f"ci_scope area '{area}' is allowlisted as informational but "
                    "now gates a job; remove it from the allowlist",
                )

    def test_informational_allowlist_entries_have_reasons(self) -> None:
        for area, reason in _INFORMATIONAL_SCOPE_AREAS.items():
            with self.subTest(area=area):
                self.assertIn(area, ci_scope._AREA_NAMES)
                self.assertTrue(reason.strip(), "allowlist entry needs a reason")

    def test_contract_suite_reports_no_failures_on_real_workflow(self) -> None:
        failures, stats = _run_contract_suite(self.workflow)
        self.assertEqual(failures, [], "\n".join(failures))
        self.assertGreaterEqual(stats["gated"], 1)
        self.assertGreaterEqual(stats["areas"], 8)

    def test_contract_suite_detects_missing_nexus_e2e_requirement(self) -> None:
        mutated = _load_workflow()
        env = _gate_env(mutated["jobs"][_QUALITY_GATE_JOB])
        for step in mutated["jobs"][_QUALITY_GATE_JOB]["steps"]:
            step_env = step.get("env") or {}
            if "INTEGRATION_REQUIRED" in step_env:
                step["env"]["INTEGRATION_REQUIRED"] = (
                    "${{ needs.scope.outputs.integration }}"
                )
                break
        self.assertIn("INTEGRATION_REQUIRED", env)
        failures, _ = _run_contract_suite(mutated)
        self.assertTrue(
            any("nexus_e2e" in failure and "quality-gate" in failure for failure in failures),
            f"expected a nexus_e2e gate failure, got: {failures}",
        )

    def test_contract_suite_detects_ungated_scope_area(self) -> None:
        with mock.patch.object(
            ci_scope,
            "_AREA_NAMES",
            ci_scope._AREA_NAMES + ("experimental",),
        ):
            failures, _ = _run_contract_suite(self.workflow)
        self.assertTrue(
            any("experimental" in failure for failure in failures),
            f"expected an ungated-area failure, got: {failures}",
        )

    def test_contract_suite_detects_gate_without_loop_entry(self) -> None:
        mutated = _load_workflow()
        for step in mutated["jobs"][_QUALITY_GATE_JOB]["steps"]:
            run = step.get("run")
            if isinstance(run, str) and "for area in" in run:
                step["run"] = run.replace(" WINDOWS_OFFLINE", "")
                break
        failures, _ = _run_contract_suite(mutated)
        self.assertTrue(
            any("WINDOWS_OFFLINE" in failure for failure in failures),
            f"expected a loop-entry failure, got: {failures}",
        )

    def test_contract_suite_detects_missing_needs(self) -> None:
        mutated = _load_workflow()
        gate = mutated["jobs"][_QUALITY_GATE_JOB]
        gate["needs"] = [n for n in gate["needs"] if n != "windows-launcher"]
        failures, _ = _run_contract_suite(mutated)
        self.assertTrue(
            any(
                "needs.<job>.result for ['windows-launcher']" in failure
                for failure in failures
            ),
            f"expected a needs failure, got: {failures}",
        )


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
import zipfile
import json
import os
import io
import re
import copy
import subprocess
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path

import yaml
import requests

import jobflow

TEST_EXPERIENCE_CV = """## Professional Experience

### Data Analyst — Example B.V.

Eindhoven, Netherlands | Jan 2020 – Dec 2020

- Built Python systems.

## Education
"""


def experience_fields(kind="none", minimum=0, count_status="excluded"):
    return {
        "experience_requirement": {"kind": kind, "minimum_months": minimum, "wording": ""},
        "experience_roles": [{
            "role": "Data Analyst — Example B.V.", "experience_type": "professional_employment",
            "relevance": "direct", "count_status": count_status,
            "evidence": ["Built Python systems."], "rationale": "Direct vacancy evidence.",
        }],
        "project_assessment": [],
    }


class JobFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = jobflow.resolved_config(yaml.safe_load((Path(__file__).parents[1] / "config.example.yaml").read_text()))
        cls.cv = "Python SQL machine learning data analytics NLP Amsterdam experience skills education summary"
        cls.sponsors = {"example tech"}

    def job(self, **updates):
        value = {
            "title": "Junior Data Scientist",
            "company": "Example Tech B.V.",
            "location": "Eindhoven, Netherlands",
            "description": "Use Python, SQL and machine learning. English role. One year experience preferred.",
            "url": "https://example.test/jobs/1",
        }
        value.update(updates)
        return value

    def test_accepts_matching_entry_role(self):
        accepted, reasons, relevance = jobflow.filter_job(self.job(), self.sponsors, self.cfg, self.cv)
        self.assertTrue(accepted, reasons)
        self.assertGreaterEqual(relevance, 75)

    def test_structured_result_keeps_unknown_material_facts(self):
        result = jobflow.filter_job(self.job(), self.sponsors, self.cfg, self.cv)
        self.assertTrue(result.eligible, result.rejection_reasons)
        self.assertEqual(result.rejection_reasons, [])
        self.assertIn("salary and applicable IND income threshold", result.verification_needed)
        self.assertIn("security screening or nationality restrictions not stated", result.verification_needed)
        self.assertIn("onsite, hybrid, or remote arrangement not stated", result.verification_needed)

    def test_configurable_applicant_and_job_gates(self):
        cases = (
            ({"description": "Python ML role. PhD required."}, {}, "required education exceeds"),
            ({"description": "Python ML role. Dutch B2 required."}, {}, "Dutch requirement exceeds"),
            ({"description": "Python ML role. NATO clearance required."}, {}, "clearance"),
            ({"description": "Python ML role. 3 years experience required."}, {}, "experience exceeds"),
            ({"description": "Python ML role. Remote position."}, {"workplaces": ["onsite"]}, "workplace arrangement"),
        )
        for updates, criteria_updates, expected in cases:
            cfg = copy.deepcopy(self.cfg)
            cfg["search_criteria"].update(criteria_updates)
            with self.subTest(expected=expected):
                result = jobflow.filter_job(self.job(**updates), self.sponsors, cfg, self.cv)
                self.assertFalse(result.eligible)
                self.assertTrue(any(expected in reason for reason in result.rejection_reasons), result.rejection_reasons)

    def test_internship_and_student_permit_rules_are_separate(self):
        cfg = copy.deepcopy(self.cfg)
        regular = self.job(title="Data Science Internship")
        self.assertFalse(jobflow.filter_job(regular, self.sponsors, cfg, self.cv).eligible)
        cfg["search_criteria"]["internships"]["regular"] = True
        self.assertTrue(jobflow.filter_job(regular, self.sponsors, cfg, self.cv).eligible)

        cfg["applicant"].update({"residence_route": "student_permit", "study_status": "enrolled"})
        full_time = self.job(employment_type="FULL_TIME")
        result = jobflow.filter_job(full_time, self.sponsors, cfg, self.cv)
        self.assertFalse(result.eligible)
        self.assertTrue(any("16 hours" in warning for warning in result.warnings))

    def test_graduation_and_enrollment_internships_are_independent(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["search_criteria"]["internships"]["graduation"] = True
        graduation = self.job(title="Graduation Internship Data Science")
        self.assertTrue(jobflow.filter_job(graduation, self.sponsors, cfg, self.cv).eligible)
        enrolled = self.job(title="Working Student Data Scientist", description="Python ML. Must be enrolled at university.")
        self.assertFalse(jobflow.filter_job(enrolled, self.sponsors, cfg, self.cv).eligible)
        cfg["applicant"]["study_status"] = "enrolled"
        cfg["search_criteria"]["internships"]["enrollment_required"] = True
        self.assertTrue(jobflow.filter_job(enrolled, self.sponsors, cfg, self.cv).eligible)

    def test_sponsor_and_security_preferences_are_configurable(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["search_criteria"]["eligibility"]["require_recognized_sponsor"] = False
        unknown = self.job(company="Unknown B.V.")
        self.assertTrue(jobflow.filter_job(unknown, set(), cfg, self.cv).eligible)
        cfg["search_criteria"]["eligibility"]["accept_security_screening"] = True
        secured = self.job(description="Python ML role. Security clearance required.")
        self.assertTrue(jobflow.filter_job(secured, set(), cfg, self.cv).eligible)

    def test_sponsor_snapshot_refresh_and_fail_closed(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA, jobflow.DB_PATH = Path(folder), Path(folder) / "jobs.sqlite3"
            conn = jobflow.db()
            current = jobflow.datetime.now(jobflow.timezone.utc)
            fresh = current.isoformat()
            conn.executemany("INSERT INTO sponsors VALUES (?,?,?,?)",
                             [(f"sponsor {index}", f"Sponsor {index}", None, fresh) for index in range(100)])
            conn.commit()
            with mock.patch.object(jobflow, "refresh_sponsors") as refresh:
                status = jobflow.ensure_sponsor_snapshot(conn, self.cfg, current)
            self.assertEqual(status["status"], "fresh")
            refresh.assert_not_called()

            stale = (current - jobflow.timedelta(days=2)).isoformat()
            conn.execute("UPDATE sponsors SET fetched_at=?", (stale,)); conn.commit()
            with mock.patch.object(jobflow, "refresh_sponsors", side_effect=RuntimeError("offline")):
                status = jobflow.ensure_sponsor_snapshot(conn, self.cfg, current)
            self.assertEqual(status["status"], "stale_fallback")

            conn.execute("DELETE FROM sponsors")
            conn.execute("INSERT INTO sponsors VALUES ('example','Example',NULL,'test')"); conn.commit()
            with mock.patch.object(jobflow, "refresh_sponsors", side_effect=RuntimeError("offline")):
                with self.assertRaisesRegex(RuntimeError, "invalid sponsor snapshot \\(1 entries\\)"):
                    jobflow.ensure_sponsor_snapshot(conn, self.cfg, current)
            conn.close()
        jobflow.DATA, jobflow.DB_PATH = old

    def test_zero_result_scan_records_empty_current_run_and_creates_no_outputs(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            marketplace = {"found": 0, "screening": 0, "rejected": 0, "duplicate": 0,
                           "unmatched": 0, "errors": 0, "screening_job_ids": []}
            output = io.StringIO()
            with mock.patch.object(jobflow, "config", return_value=self.cfg), \
                 mock.patch.object(jobflow, "ensure_sponsor_snapshot",
                                   return_value={"count": 100, "fetched_at": "now", "status": "fresh"}), \
                 mock.patch.object(jobflow, "master_cv", return_value=self.cv), \
                 mock.patch.object(jobflow, "discover_marketplaces", return_value=marketplace), \
                 redirect_stdout(output):
                jobflow.scan()
            summary = json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(summary["found"], 0)
            self.assertEqual(summary["screening_job_ids"], [])
            self.assertIsInstance(summary["scan_run_id"], int)
            conn = jobflow.db()
            run = conn.execute("SELECT screening_job_ids FROM scan_runs WHERE id=?",
                               (summary["scan_run_id"],)).fetchone()
            self.assertEqual(json.loads(run[0]), [])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM telegram_deliveries").fetchone()[0], 0)
            conn.close()
            self.assertFalse(jobflow.ARTIFACTS.exists())
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_selected_city_group_includes_veldhoven(self):
        result = jobflow.filter_job(self.job(location="Veldhoven, Netherlands"), self.sponsors, self.cfg, self.cv)
        self.assertTrue(result.eligible, result.rejection_reasons)

    def test_config_validation_fails_with_actionable_message(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["applicant"]["dutch_level"] = "fluent-ish"
        with self.assertRaisesRegex(SystemExit, "applicant.dutch_level"):
            jobflow.validate_config(cfg)

    def test_config_layers_defaults_preset_user_and_override(self):
        user = yaml.safe_load((jobflow.ROOT / "config.example.yaml").read_text())
        cfg = jobflow.resolved_config(user)
        self.assertEqual(cfg["marketplace_discovery"]["queries"][0], "Data Scientist")
        self.assertIn("ASML", {item["name"] for item in cfg["priority_companies"]})
        self.assertIn("groups", cfg["search_criteria"]["locations"])

    def test_unknown_preset_lists_available_presets(self):
        user = yaml.safe_load((jobflow.ROOT / "config.example.yaml").read_text())
        user["search_criteria"]["preset"] = "unknown"
        with self.assertRaisesRegex(SystemExit, "unknown legacy preset"):
            jobflow.resolved_config(user)

    def test_preset_validation_rejects_missing_invalid_regex_and_unsafe_paths(self):
        preset = yaml.safe_load((jobflow.ROOT / "presets" / "data_ai.yaml").read_text())
        missing = copy.deepcopy(preset)
        missing.pop("label")
        with self.assertRaisesRegex(SystemExit, "requires valid fields: label"):
            jobflow.validate_preset(missing, Path("broken.yaml"))
        bad_regex = copy.deepcopy(preset)
        bad_regex["role_fit"]["clear_title"] = "["
        with self.assertRaisesRegex(SystemExit, "invalid regex"):
            jobflow.validate_preset(bad_regex, Path("broken.yaml"))
        unsafe = copy.deepcopy(preset)
        unsafe["cv_references"] = {"data-scientist": "../outside.pdf"}
        with self.assertRaisesRegex(SystemExit, "invalid values"):
            jobflow.validate_preset(unsafe, Path("broken.yaml"))

    def test_software_preset_uses_shared_screening_without_data_ai_leaks(self):
        user = yaml.safe_load((jobflow.ROOT / "config.example.yaml").read_text())
        user["search_criteria"]["preset"] = "software_engineering"
        software = jobflow.resolved_config(user)
        vacancy = self.job(
            title="Junior Backend Developer",
            description="Build and test backend APIs and database integrations. One year experience preferred.",
        )
        self.assertTrue(jobflow.filter_job(vacancy, self.sponsors, software, self.cv).eligible)
        data_result = jobflow.filter_job(vacancy, self.sponsors, self.cfg, self.cv)
        self.assertFalse(data_result.eligible)
        self.assertIn("lacks Data Science and AI relevance", data_result.rejection_reasons)

    def test_multi_study_roles_deduplicate_and_drive_soft_filter(self):
        user = yaml.safe_load((jobflow.ROOT / "config.example.yaml").read_text())
        user["search_criteria"]["study_profiles"] = ["data_science_ai", "computer_science", "statistics"]
        user["search_criteria"]["roles"] = ["data_scientist", "software_engineer"]
        cfg = jobflow.resolved_config(user)
        self.assertEqual(cfg["marketplace_discovery"]["queries"].count("Data Scientist"), 1)
        deselected = self.job(title="Data Analyst", description="Data science and reporting role.")
        result = jobflow.filter_job(deselected, self.sponsors, cfg, self.cv)
        self.assertIn("role intentionally deselected", result.rejection_reasons)
        adjacent = self.job(title="Risk Traineeship", description="Statistical modelling and econometrics.")
        result = jobflow.filter_job(adjacent, self.sponsors, cfg, self.cv)
        self.assertTrue(result.eligible, result.rejection_reasons)
        self.assertIn("role title requires fit verification", result.verification_needed)

    def test_study_union_applies_only_matching_cv_rules(self):
        user = yaml.safe_load((jobflow.ROOT / "config.example.yaml").read_text())
        user["search_criteria"]["study_profiles"] = ["computer_science", "statistics"]
        user["search_criteria"]["roles"] = ["software_engineer", "data_scientist"]
        cfg = jobflow.resolved_config(user)
        cv = ("# Name\n## Summary\nx\n## Experience\nx\n## Projects\nx\n## Education\n"
              "*MSc Data Science | 2020 - 2022*\nSchool\n## Skills\n**Programming:** Python\n"
              "**Testing:** pytest\n## Languages\nEnglish (Fluent)")
        self.assertIn("MSc thesis must be a bullet directly below education item",
                      jobflow.document_preflight(cv, "cv", {}, cfg))

    def test_summary_bank_role_warnings_do_not_edit_master_cv(self):
        user = yaml.safe_load((jobflow.ROOT / "config.example.yaml").read_text())
        user["search_criteria"]["roles"] = ["data_scientist", "data_analyst"]
        cfg = jobflow.resolved_config(user)
        warnings = jobflow.summary_bank_role_warnings(
            cfg, "## Professional Summary Bank\n\n### Data Scientist\n\nSupported.\n\n### Statistician\n\nSupported.\n")
        self.assertTrue(any("Data Analyst" in warning for warning in warnings))
        self.assertTrue(any("Statistician" in warning for warning in warnings))

    def test_preset_specific_cv_rules_do_not_leak_to_software(self):
        user = yaml.safe_load((jobflow.ROOT / "config.example.yaml").read_text())
        user["search_criteria"]["preset"] = "software_engineering"
        software = jobflow.resolved_config(user)
        cv = ("# Name\n## Summary\nx\n## Experience\nx\n## Projects\nx\n## Education\n"
              "*MSc Data Science | 2020 - 2022*\nSchool\n## Skills\n**Programming:** Python\n"
              "**Testing:** pytest\n## Languages\nEnglish (Fluent)\nimbalanced-classification")
        failures = jobflow.document_preflight(cv, "cv", {}, software)
        self.assertFalse(any("thesis" in item.lower() or "extraction-risk" in item for item in failures))
        self.assertEqual(jobflow.cv_reference("Backend Engineer", software).name, "cv-data-scientist.pdf")
        self.assertEqual(jobflow.cv_role("Frontend Engineer", software), "frontend-engineer")
        self.assertNotEqual(jobflow.general_cv_prompt_digest(software), jobflow.general_cv_prompt_digest(self.cfg))

    def test_relevance_uses_title_and_description_concepts(self):
        cases = (
            {"title": "Risk Traineeship", "description": "Work on econometrics for portfolio risk."},
            {"title": "Risk Traineeship", "description": "Use process mining to improve finance workflows."},
        )
        for updates in cases:
            with self.subTest(updates=updates):
                accepted, reasons, _ = jobflow.filter_job(self.job(**updates), self.sponsors, self.cfg, self.cv)
                self.assertTrue(accepted, reasons)

    def test_weak_relevance_terms_do_not_pass_without_strong_concept(self):
        cases = (
            {"title": "Risk Traineeship", "description": "Build reporting in SQL for finance teams."},
            {"title": "Junior Analyst", "description": "Build statistical models for planning."},
        )
        for updates in cases:
            with self.subTest(updates=updates):
                accepted, reasons, _ = jobflow.filter_job(self.job(**updates), self.sponsors, self.cfg, self.cv)
                self.assertFalse(accepted)
                self.assertIn("lacks Data Science and AI relevance", reasons)

    def test_rejects_jobs_without_data_ai_relevance(self):
        accepted, reasons, _ = jobflow.filter_job(
            self.job(title="Risk Traineeship", description="Rotate through finance teams and stakeholder meetings."),
            self.sponsors, self.cfg, self.cv,
        )
        self.assertFalse(accepted)
        self.assertIn("lacks Data Science and AI relevance", reasons)

    def test_review_anyway_bypasses_only_relevance(self):
        plain = self.job(title="Risk Traineeship", description="Rotate through finance teams.")
        accepted, reasons, _ = jobflow.filter_job(plain, self.sponsors, self.cfg, self.cv, review_anyway=True)
        self.assertTrue(accepted, reasons)

        hard_blockers = (
            {"title": "Senior Risk Traineeship"},
            {"employment_type": "PART_TIME"},
            {"title": "Working Student Risk Traineeship"},
            {"location": "Paris, France"},
            {"company": "Unknown B.V."},
            {"description": "Visa sponsorship cannot be provided."},
        )
        for updates in hard_blockers:
            with self.subTest(updates=updates):
                accepted, reasons, _ = jobflow.filter_job(
                    self.job(**updates), self.sponsors, self.cfg, self.cv, review_anyway=True)
                self.assertFalse(accepted)
                self.assertNotIn("lacks Data Science and AI relevance", reasons)

    def test_legal_name_alias_matches(self):
        self.assertTrue(jobflow.sponsor_matches(
            "KLM", {"koninklijke luchtvaart maatschappij"}, self.cfg
        ))

    def test_priority_source_urls_are_current(self):
        urls = {item["name"]: item["career_url"] for item in self.cfg["priority_companies"]}
        self.assertEqual(urls["Booking.com"], "https://jobs.booking.com/booking/jobs?location=Netherlands")
        self.assertEqual(urls["Philips"], "https://www.careers.philips.com/nl/en/search-results")
        self.assertEqual(urls["ABN AMRO"], "https://www.werkenbijabnamro.nl/vacatures/land/nederland")
        self.assertIn("linkedin.com/jobs-guest/", urls["KLM"])
        self.assertIn("linkedin.com/jobs-guest/", urls["Rabobank"])
        self.assertIn("linkedin.com/jobs-guest/", urls["Coolblue"])
        self.assertEqual(urls["Airbus"], "https://ag.wd3.myworkdayjobs.com/Airbus")
        self.assertEqual(urls["Deloitte"], "https://www.deloitte.com/nl/en/careers.html")
        self.assertEqual(urls["PwC"], "https://www.pwc.nl/careers")
        self.assertEqual(urls["Capgemini"], "https://www.capgemini.com/careers/join-capgemini/job-search/")
        self.assertEqual(urls["Bitvavo"], "https://jobs.bitvavo.com/find-your-role")
        self.assertEqual(urls["Uber"], "https://jobs.uber.com/en/jobs/?location=Amsterdam")
        self.assertEqual(urls["Heineken"], "https://careers.theheinekencompany.com/Job-Listing?field_location_country_code_1[]=NL")
        self.assertEqual(urls["Klarna"], "https://jobs.deel.com/klarna?locationIds[]=a013b13b-89f0-4d4b-9675-88594255fd0c")
        self.assertIn("linkedin.com/jobs-guest/", urls["Tesla"])
        self.assertIn("linkedin.com/jobs-guest/", urls["McKinsey"])

    def test_linkedin_fallback_keeps_only_exact_employer(self):
        listing = '''
        <li><h3>Junior Data Analyst</h3><h4>Rabobank</h4><a href="https://nl.linkedin.com/jobs/view/123?tracking=x">Job</a></li>
        <li><h4>Recruiter</h4><a href="https://nl.linkedin.com/jobs/view/456">Job</a></li>
        '''
        with mock.patch.object(jobflow, "fetch", return_value=listing) as fetch:
            jobs, complete = jobflow.scrape_source(
                "Rabobank", "https://www.linkedin.com/jobs-guest/jobs/api/search")
        self.assertEqual((len(jobs), jobs[0]["company"], complete), (1, "Rabobank", True))
        self.assertEqual(jobs[0]["title"], "Junior Data Analyst")
        self.assertEqual(fetch.call_count, 1)

    def test_linkedin_apply_url_skips_easy_apply_and_extracts_official_url(self):
        easy = '<a href="https://www.linkedin.com/jobs/view/1">Easy Apply</a>'
        self.assertIsNone(jobflow.linkedin_apply_url("https://linkedin.com/jobs/view/1", easy))
        external = '<a href="https://company.example/jobs/1">Apply</a>'
        self.assertEqual(
            jobflow.linkedin_apply_url("https://linkedin.com/jobs/view/1", external),
            "https://company.example/jobs/1",
        )
        redirect = '<a href="https://www.linkedin.com/jobs/view/externalApply/1?url=https%3A%2F%2Fcompany.example%2Fjobs%2F2">Apply</a>'
        self.assertEqual(
            jobflow.linkedin_apply_url("https://linkedin.com/jobs/view/1", redirect),
            "https://company.example/jobs/2",
        )

    def test_linkedin_source_does_not_fetch_detail_pages(self):
        listing = '<li><h3>Junior Data Analyst</h3><h4>Rabobank</h4><a href="https://nl.linkedin.com/jobs/view/123">Job</a></li>'
        with mock.patch.object(jobflow, "fetch", return_value=listing) as fetch:
            jobs, complete = jobflow.scrape_source(
                "Rabobank", "https://www.linkedin.com/jobs-guest/jobs/api/search")
        self.assertTrue(complete)
        self.assertEqual(jobs[0]["url"], "https://nl.linkedin.com/jobs/view/123")
        self.assertEqual(fetch.call_count, 1)

    def test_linkedin_apply_url_browser_failure_returns_none(self):
        document = '<button id="topbar-apply">Apply</button>'
        with mock.patch.object(jobflow, "browser_fetch", side_effect=RuntimeError("blocked")):
            self.assertIsNone(jobflow.linkedin_apply_url("https://linkedin.com/jobs/view/1", document))

    def test_marketplace_parsers_canonicalize_urls_and_age_filter(self):
        detail = '''<script type="application/ld+json">{
          "@type":"JobPosting", "title":"Data Analyst", "description":"Python SQL",
          "hiringOrganization":{"name":"Example Tech"},
          "jobLocation":{"address":{"addressLocality":"Utrecht","addressCountry":"NL"}}
        }</script>'''
        linkedin = '<li><a href="https://nl.linkedin.com/jobs/view/data-123?tracking=x">Job</a></li>'
        indeed = '<div data-jk="abc123"><a href="/viewjob?jk=abc123">Job</a></div>'

        def fake_fetch(url, **kwargs):
            if "/jobs-guest/" in url:
                self.assertIn("f_TPR=r86400", url)
                return linkedin
            if "indeed.com/jobs?" in url:
                self.assertIn("fromage=1", url)
                return indeed
            return detail

        with mock.patch.object(jobflow, "fetch", side_effect=fake_fetch):
            linkedin_jobs = jobflow.marketplace_jobs("linkedin", ["Data Analyst"], 10)
            indeed_jobs = jobflow.marketplace_jobs("indeed", ["Data Analyst"], 10)
        self.assertEqual(linkedin_jobs[0]["url"], "https://nl.linkedin.com/jobs/view/data-123")
        self.assertEqual(indeed_jobs[0]["url"], "https://nl.indeed.com/viewjob?jk=abc123")

    def test_linkedin_marketplace_does_not_resolve_apply_with_browser(self):
        search = '<li><a href="https://nl.linkedin.com/jobs/view/data-123?tracking=x">Job</a></li>'
        detail = '''<button id="topbar-apply">Apply</button>
        <script type="application/ld+json">{
          "@type":"JobPosting", "title":"Data Analyst", "description":"Python SQL",
          "hiringOrganization":{"name":"Example Tech"},
          "jobLocation":"Utrecht, Netherlands",
          "url":"https://nl.linkedin.com/jobs/view/data-123"
        }</script>'''

        def fake_fetch(url, **kwargs):
            return search if "/jobs-guest/" in url else detail

        with mock.patch.object(jobflow, "fetch", side_effect=fake_fetch), \
             mock.patch.object(jobflow, "browser_fetch", side_effect=AssertionError("browser should not run")):
            jobs = jobflow.marketplace_jobs("linkedin", ["Data Analyst"], 10)
        self.assertEqual(jobs[0]["url"], "https://nl.linkedin.com/jobs/view/data-123")

    def test_indeed_search_uses_browser_fallback_after_403(self):
        response = requests.Response()
        response.status_code = 403
        error = requests.HTTPError("forbidden", response=response)
        detail = '''<script type="application/ld+json">{
          "@type":"JobPosting", "title":"Data Analyst", "description":"Python",
          "hiringOrganization":{"name":"Example"}, "jobLocation":"Netherlands"
        }</script>'''

        def fake_fetch(url, **kwargs):
            if "indeed.com/jobs?" in url:
                raise error
            return detail

        with mock.patch.object(jobflow, "fetch", side_effect=fake_fetch), \
             mock.patch.object(jobflow, "browser_fetch", return_value='<div data-jk="abc123">Job</div>'):
            jobs = jobflow.marketplace_jobs("indeed", ["Data Analyst"], 10)
        self.assertEqual(jobs[0]["url"], "https://nl.indeed.com/viewjob?jk=abc123")

    def test_indeed_blocked_search_is_reported_as_source_error(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA, jobflow.DB_PATH = Path(folder), Path(folder) / "jobs.sqlite3"
            conn = jobflow.db()
            cfg = {**self.cfg, "marketplace_discovery": {
                "enabled": True, "queries": ["Data Analyst"],
                "max_results_per_source": 10, "max_age_hours": 24}}
            matched = self.job(company="Example Tech", url="https://linkedin.com/jobs/view/1")
            with mock.patch.object(jobflow, "marketplace_jobs", side_effect=[
                [matched], jobflow.MarketplaceFetchError("indeed", "blocked", "https://nl.indeed.com/jobs")
            ]):
                totals = jobflow.discover_marketplaces(conn, cfg, {"example tech"}, self.cv)
            self.assertEqual((totals["screening"], totals["errors"]), (1, 1))
            conn.close()
        jobflow.DATA, jobflow.DB_PATH = old

    def test_indeed_blocked_detail_is_skipped(self):
        response = requests.Response()
        response.status_code = 403
        error = requests.HTTPError("forbidden", response=response)
        detail = '''<script type="application/ld+json">{
          "@type":"JobPosting", "title":"Data Analyst", "description":"Python",
          "hiringOrganization":{"name":"Example"}, "jobLocation":"Netherlands"
        }</script>'''

        def fake_fetch(url, **kwargs):
            if "indeed.com/jobs?" in url:
                return '<div data-jk="abc123">Job</div><div data-jk="def456">Job</div>'
            if "def456" in url:
                return detail
            raise error

        with mock.patch.object(jobflow, "fetch", side_effect=fake_fetch):
            jobs = jobflow.marketplace_jobs("indeed", ["Data Analyst"], 10)
        self.assertEqual([job["url"] for job in jobs], ["https://nl.indeed.com/viewjob?jk=def456"])

        def all_blocked_fetch(url, **kwargs):
            if "indeed.com/jobs?" in url:
                return '<div data-jk="abc123">Job</div>'
            raise error

        with mock.patch.object(jobflow, "fetch", side_effect=all_blocked_fetch):
            with self.assertRaises(jobflow.MarketplaceFetchError):
                jobflow.marketplace_jobs("indeed", ["Data Analyst"], 10)

    def test_marketplace_paginates_to_bounded_query_share(self):
        detail = '''<script type="application/ld+json">{
          "@type":"JobPosting", "title":"Data Analyst", "description":"Python",
          "hiringOrganization":{"name":"Example"}, "jobLocation":"Netherlands"
        }</script>'''

        def fake_fetch(url, **kwargs):
            if "/jobs-guest/" not in url:
                return detail
            start = int(jobflow.parse_qs(jobflow.urlparse(url).query)["start"][0])
            return (f'<li><a href="https://linkedin.com/jobs/view/{start + 1}">Job</a></li>'
                    if start < 2 else "")

        with mock.patch.object(jobflow, "fetch", side_effect=fake_fetch):
            jobs = jobflow.marketplace_jobs("linkedin", ["Data Analyst"], 2)
        self.assertEqual([job["url"] for job in jobs],
                         ["https://linkedin.com/jobs/view/1", "https://linkedin.com/jobs/view/2"])

    def test_marketplace_matching_is_exact_or_explicit_alias_only(self):
        sponsors = {"example technology", "acme bank"}
        cfg = {"sponsor_aliases": {"acme": "acme bank"}}
        self.assertEqual(jobflow.strict_sponsor_key("Example Technology B.V.", sponsors, cfg),
                         "example technology")
        self.assertEqual(jobflow.strict_sponsor_key("Acme", sponsors, cfg), "acme bank")
        self.assertIsNone(jobflow.strict_sponsor_key("Example Tech", sponsors, cfg))

    def test_marketplace_discovery_quarantines_uncertain_employers(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA, jobflow.DB_PATH = Path(folder), Path(folder) / "jobs.sqlite3"
            conn = jobflow.db()
            cfg = {**self.cfg, "marketplace_discovery": {
                "enabled": True, "queries": ["Data Analyst"],
                "max_results_per_source": 10, "max_age_hours": 24}}
            matched = self.job(company="Example Tech", url="https://linkedin.com/jobs/view/1")
            uncertain = self.job(company="Example", url="https://linkedin.com/jobs/view/2")
            with mock.patch.object(jobflow, "marketplace_jobs", side_effect=[[matched, uncertain], []]):
                totals = jobflow.discover_marketplaces(conn, cfg, {"example tech"}, self.cv)
            self.assertEqual((totals["screening"], totals["unmatched"]), (1, 1))
            self.assertEqual(conn.execute("SELECT discovery_source FROM jobs").fetchone()[0], "linkedin")
            quarantined = conn.execute(
                "SELECT company,status FROM lead_imports WHERE status='unmatched_sponsor'").fetchone()
            self.assertEqual(tuple(quarantined), ("Example", "unmatched_sponsor"))
            conn.close()
        jobflow.DATA, jobflow.DB_PATH = old

    def test_agent_marketplace_results_use_shared_pipeline_and_other_source_falls_back(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA, jobflow.DB_PATH = Path(folder), Path(folder) / "jobs.sqlite3"
            path = Path(folder) / "indeed.json"
            path.write_text(json.dumps([self.job(url="https://nl.indeed.com/viewjob?jk=abc")]))
            conn = jobflow.db()
            cfg = {**self.cfg, "marketplace_discovery": {
                "enabled": True, "queries": ["Data Analyst"],
                "max_results_per_source": 10, "max_age_hours": 24}}
            with mock.patch.object(jobflow, "marketplace_jobs", return_value=[]) as fallback:
                totals = jobflow.discover_marketplaces(
                    conn, cfg, {"example tech"}, self.cv, {"indeed": path})
            self.assertEqual((totals["found"], totals["screening"]), (1, 1))
            self.assertEqual(totals["screening_job_ids"], [jobflow.job_id(self.job(
                url="https://nl.indeed.com/viewjob?jk=abc"))])
            fallback.assert_called_once()
            self.assertEqual(fallback.call_args.args[0], "linkedin")
            self.assertEqual(conn.execute("SELECT discovery_source FROM jobs").fetchone()[0], "indeed")
            conn.close()
        jobflow.DATA, jobflow.DB_PATH = old

    def test_agent_marketplace_results_reject_wrong_host(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "indeed.json"
            path.write_text(json.dumps([self.job(url="https://example.test/jobs/1")]))
            with self.assertRaisesRegex(ValueError, "not from indeed.com"):
                jobflow.marketplace_jobs_from_file("indeed", path, 10)

    def test_fetch_retries_timeout_and_server_errors(self):
        response = mock.Mock(status_code=200, text="x " * 400)
        with mock.patch.object(jobflow.requests, "get", side_effect=[requests.Timeout(), response]) as get, \
             mock.patch.object(jobflow.time, "sleep"):
            self.assertEqual(jobflow.fetch("https://example.test"), response.text)
        self.assertEqual(get.call_count, 2)

        busy = mock.Mock(status_code=503)
        with mock.patch.object(jobflow.requests, "get", side_effect=[busy, busy, response]) as get, \
             mock.patch.object(jobflow.time, "sleep"):
            jobflow.fetch("https://example.test")
        self.assertEqual(get.call_count, 3)

    def test_fetch_403_uses_browser_once_and_stops_on_challenge(self):
        forbidden = mock.Mock(status_code=403)
        with mock.patch.object(jobflow.requests, "get", return_value=forbidden) as get, \
             mock.patch.object(jobflow, "browser_fetch", return_value="rendered " * 100) as browser:
            self.assertIn("rendered", jobflow.fetch("https://example.test"))
        self.assertEqual((get.call_count, browser.call_count), (1, 1))

        with mock.patch.object(jobflow.requests, "get", return_value=forbidden), \
             mock.patch.object(jobflow, "browser_fetch", side_effect=RuntimeError("browser challenge blocked")) as browser:
            with self.assertRaisesRegex(RuntimeError, "challenge"):
                jobflow.fetch("https://example.test")
        self.assertEqual(browser.call_count, 1)

    def test_scraper_follows_explicit_external_ats_link_only(self):
        listing = ('<a href="https://ats.example/jobs/1">View vacancy</a>'
                   '<a href="https://linkedin.com/jobs/2">LinkedIn job</a>')
        vacancy = json.dumps({"@type": "JobPosting", "title": "Data Analyst",
            "hiringOrganization": {"name": "Example"}, "jobLocation": "Netherlands",
            "description": "Python SQL analytics", "url": "https://ats.example/jobs/1"})
        page = f'<script type="application/ld+json">{vacancy}</script>'
        with mock.patch.object(jobflow, "fetch", side_effect=[listing, page]) as fetch:
            jobs, _ = jobflow.scrape_source("Example", "https://company.example/careers")
        self.assertEqual([job["url"] for job in jobs], ["https://ats.example/jobs/1"])
        self.assertEqual(fetch.call_count, 2)

    def test_netherlands_location_uses_description_only_when_location_missing(self):
        self.assertTrue(jobflow.job_in_netherlands(self.job(location="Amsterdam")))
        self.assertFalse(jobflow.job_in_netherlands(self.job(location="Paris", description="Team also works in Amsterdam")))
        self.assertTrue(jobflow.job_in_netherlands(self.job(location="", description="Role based in Utrecht")))

    def test_rejects_experience_over_one_year(self):
        cases = (
            "Requires at least 2 years of professional experience with Python SQL machine learning data analytics NLP.",
            "5+ years of hands-on experience with Python SQL machine learning data analytics NLP.",
            "More than 4 years of working experience with Python SQL machine learning data analytics NLP.",
            "Over 4 years of professional experience with Python SQL machine learning data analytics NLP.",
            "You have at least 2 years fulltime experience as a Product Owner of multiple teams. Data analytics.",
            "Minimum 10 years’ experience in a complex accounting environment with data reporting.",
            "Minimaal 3 jaar relevante werkervaring met Python SQL machine learning data analytics NLP.",
        )
        for description in cases:
            with self.subTest(description=description):
                accepted, reasons, _ = jobflow.filter_job(self.job(description=description), self.sponsors, self.cfg, self.cv)
                self.assertFalse(accepted)
                self.assertTrue(any("exceeds" in reason for reason in reasons), reasons)

    def test_rejects_seniority_titles_before_relevance(self):
        titles = ("Senior Data Scientist", "Sr. ML Engineer", "Medior Data Analyst",
                  "Data Science Lead", "Principal AI Engineer", "Staff Data Engineer",
                  "Head of Analytics", "Data Director", "AI Manager", "Data Architect",
                  "VP Data", "Chief Data Officer", "CTO")
        for title in titles:
            with self.subTest(title=title):
                accepted, reasons, relevance = jobflow.filter_job(
                    self.job(title=title), self.sponsors, self.cfg, "")
                self.assertFalse(accepted)
                self.assertEqual(relevance, 0)
                self.assertTrue(reasons[0].startswith("seniority title excluded:"))

    def test_seniority_filter_uses_word_boundaries(self):
        accepted, reasons, _ = jobflow.filter_job(
            self.job(title="Leadership Data Scientist"), self.sponsors, self.cfg, self.cv)
        self.assertTrue(accepted, reasons)

    def test_rejects_seniority_hidden_in_description(self):
        accepted, reasons, _ = jobflow.filter_job(
            self.job(
                title="Data Modeler",
                description="Medior Data Analyst / Data Modeler. Python SQL machine learning data analytics NLP.",
            ),
            self.sponsors,
            self.cfg,
            self.cv,
        )
        self.assertFalse(accepted)
        self.assertTrue(any(reason.startswith("seniority description excluded:") for reason in reasons), reasons)

    def test_accepts_one_to_three_year_range(self):
        accepted, reasons, _ = jobflow.filter_job(
            self.job(description="Seeking 1-3 years of experience with Python SQL machine learning data analytics NLP."),
            self.sponsors, self.cfg, self.cv,
        )
        self.assertTrue(accepted, reasons)

    def test_accepts_maximum_one_year_experience_cap(self):
        accepted, reasons, _ = jobflow.filter_job(
            self.job(description="Maximum 1 year of working experience with Python SQL machine learning data analytics NLP."),
            self.sponsors, self.cfg, self.cv,
        )
        self.assertTrue(accepted, reasons)

    def test_rejects_dutch_fluency_and_security_screening(self):
        cases = (
            "Fluent Dutch required. Security clearance required. Python SQL data machine learning NLP analytics.",
            "Vloeiend in Nederlands in woord en geschrift. Python SQL data machine learning NLP analytics.",
            "Goede beheersing van de Nederlandse taal. Python SQL data machine learning NLP analytics.",
            "Fluency in Dutch and English mandatory. Python SQL data machine learning NLP analytics.",
            "Dutch language fluency. Python SQL data machine learning NLP analytics.",
            "Languages: Dutch: minimum B2 required English: minimum B required. Python SQL data analytics.",
        )
        for description in cases:
            with self.subTest(description=description):
                accepted, reasons, _ = jobflow.filter_job(self.job(description=description), self.sponsors, self.cfg, self.cv)
                self.assertFalse(accepted)
                self.assertTrue(any("Dutch" in reason for reason in reasons), reasons)
        accepted, reasons, _ = jobflow.filter_job(
            self.job(description="Security clearance required. Python SQL data machine learning NLP analytics."),
            self.sponsors, self.cfg, self.cv,
        )
        self.assertFalse(accepted)
        self.assertTrue(any("screening" in reason for reason in reasons))

    def test_rejects_ineligible_employment_constraints(self):
        cases = (
            ("Visa", {"description": "Visa sponsorship cannot be provided. Python SQL machine learning."}),
            ("Internship", {"title": "Data Science Internship"}),
            ("Internship", {"title": "Stage Business Analist Select"}),
            ("Internship", {"title": "Stagiair Category Development"}),
            ("Part-time", {"employment_type": "PART_TIME"}),
            ("Part-time", {"title": "Bijbaan Control Room Operator Retouren"}),
            ("Enrollment", {"description": "You must be enrolled at a university during employment."}),
            ("Enrollment", {"title": "Working Student Data Analyst"}),
        )
        for expected, updates in cases:
            with self.subTest(expected=expected):
                accepted, reasons, _ = jobflow.filter_job(
                    self.job(**updates), self.sponsors, self.cfg, self.cv)
                self.assertFalse(accepted)
                self.assertTrue(any(expected.lower() in reason.lower() for reason in reasons), reasons)

    def test_rejects_non_fit_role_families(self):
        cases = (
            "Product Owner",
            "Financial Accounting Expert | Lending & TS Accounting Services",
            "Process Engineer",
            "Technology Recruiter",
            "Enterprise Account Executive - DACH - Amsterdam",
            "Campagne Specialist Advertising",
            "Lab technician",
            "Network Engineer - Core Network Team",
        )
        for title in cases:
            with self.subTest(title=title):
                accepted, reasons, _ = jobflow.filter_job(
                    self.job(title=title, description="Work with data, analytics, Python and reporting."),
                    self.sponsors, self.cfg, self.cv)
                self.assertFalse(accepted)
                self.assertTrue(reasons)

    def test_keeps_clear_data_ai_titles_despite_adjacent_terms(self):
        cases = (
            "Data Scientist Commercial planning",
            "AI Platform Engineer",
            "Research Science Analyst - Energy Storage Insights",
            "FinTech Process Analyst — CDD & Transaction Monitoring",
        )
        for title in cases:
            with self.subTest(title=title):
                accepted, reasons, _ = jobflow.filter_job(
                    self.job(title=title, description="Use Python SQL data analytics modelling and reporting."),
                    self.sponsors, self.cfg, self.cv)
                self.assertTrue(accepted, reasons)

    def test_full_time_jobs_have_screening_priority(self):
        self.assertEqual(jobflow.job_priority(self.job(employment_type="FULL_TIME")), 0)
        self.assertEqual(jobflow.job_priority(self.job()), 1)
        recent = self.job(posted_at="2026-07-08")
        older = self.job(posted_at="2026-07-01", employment_type="FULL_TIME")
        self.assertLess(jobflow.screening_priority(recent, 90), jobflow.screening_priority(older, 80))
        self.assertLess(jobflow.screening_priority(recent, 80), jobflow.screening_priority(older, 80))

    def test_posting_date_is_extracted_and_normalized(self):
        document = """<script type="application/ld+json">{
          "@type":"JobPosting","title":"Data Scientist","datePosted":"2026-07-04T12:00:00+02:00",
          "description":"Python","url":"https://example.test/job"
        }</script>"""
        jobs = jobflow.jsonld_jobs(jobflow.BeautifulSoup(document, "html.parser"), "https://example.test/job")
        self.assertEqual(jobs[0]["posted_at"], "2026-07-04")
        today = jobflow.datetime(2026, 7, 8).date()
        self.assertEqual(jobflow.normalize_posted_at("Posted 2 days ago", today), "2026-07-06")
        self.assertEqual(jobflow.posting_date_text("2026-07-07", today), "2026-07-07 (1 day ago)")

    def test_sponsorship_gate_ignores_positive_offer(self):
        accepted, reasons, _ = jobflow.filter_job(
            self.job(description="Visa sponsorship is provided. Python SQL machine learning analytics NLP."),
            self.sponsors, self.cfg, self.cv,
        )
        self.assertTrue(accepted, reasons)

    def test_score_penalizes_unsupported_number(self):
        score, details = jobflow.keyword_score(
            "# Summary\n# Skills\n# Experience\n# Education\nPython SQL machine learning 999 users " + "word " * 400,
            "Python SQL machine learning", self.cv,
        )
        self.assertLess(score, 90)
        self.assertIn("999", details["unsupported_numbers"])

    def test_pdf_page_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            compact = folder / "compact.md"
            compact.write_text("# Alex Example — Data Scientist\n\n## SUMMARY\n\nPython and SQL.\n")
            compact_layout = jobflow.render_pdf(compact, compact.with_suffix(".pdf"))
            self.assertEqual(compact_layout["pages"], 1)
            self.assertTrue(compact.with_suffix(".docx").exists())

            core = ("# Alex Example\nalex@example.com | +31 6 00000000 | Amsterdam\n\n"
                    "## Summary\nPython data analyst.\n\n{work}"
                    "## Education\n*Data Science Certificate | 2023*\nUniversity\n\n"
                    "## Skills\n**Programming:** Python\n**Analytics:** SQL\n\n"
                    "## Languages\nEnglish (Fluent)")
            fixtures = {
                "experience": "## Experience\n" + "\n".join(
                    f"*Data Analyst {index} | 202{index} - 202{index + 1}*\nCompany {index}\n- Built analytics report {index}.\n"
                    for index in range(1, 5)),
                "projects": "## Projects\n" + "\n".join(
                    f"*Analytics Project {index}*\n- Built Python model and report {index}.\n" for index in range(1, 5)),
            }
            for kind, work in fixtures.items():
                source = folder / f"{kind}.md"
                source.write_text(core.format(work=work + "\n"))
                brief = {"source_item_counts": {"experience": int(kind == "experience") * 4,
                                                 "projects": int(kind == "projects") * 4},
                         "generation_constraints": {
                             "required_cv_sections": ["Summary", "Education", "Skills", "Languages"]}}
                self.assertFalse(jobflow.document_preflight(source.read_text(), "cv", brief, self.cfg))
                self.assertEqual(jobflow.render_pdf(source, source.with_suffix(".pdf"))["pages"], 1)

            overflow = folder / "overflow.md"
            overflow.write_text("# Alex Example\n\n## EXPERIENCE\n\n" + "\n".join("- Evidence" for _ in range(400)))
            self.assertGreater(jobflow.render_pdf(overflow, overflow.with_suffix(".pdf"))["pages"], 1)

    def test_docx_renderer_uses_word_style_structure(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "cv.md"
            source.write_text(
                "# Alex Example — Data Scientist\n"
                "Eindhoven | alex@example.com | +31 6 00000000\n\n"
                "## SUMMARY\n"
                "Data scientist with Python and SQL.\n\n"
                "## PROJECTS\n"
                "Fraud Detection ML Pipeline | IEEE-CIS\n"
                "- Built reliable fraud analytics workflow.\n\n"
                "## LANGUAGES\n"
                "English (Fluent), Dutch (A2)\n"
            )
            destination = source.with_suffix(".docx")
            jobflow.write_docx_from_markdown(source, destination, "cv")
            with zipfile.ZipFile(destination) as docx:
                document = docx.read("word/document.xml").decode()
                numbering = docx.read("word/numbering.xml").decode()
            self.assertIn("Alex Example", document)
            self.assertNotIn("Data Scientist", document)
            self.assertIn("SUMMARY", document)
            self.assertIn("<w:numPr>", document)
            self.assertIn("<w:numFmt w:val=\"bullet\"/>", numbering)

    def test_cv_reference_defaults_all_roles_and_allows_role_override(self):
        for title in ("Machine Learning Engineer", "Junior Data Analyst", "Data Platform Engineer",
                      "Analytics Consultant", "Backend Engineer"):
            with self.subTest(title=title):
                self.assertEqual(jobflow.cv_reference(title).name, "cv-data-scientist.pdf")
                self.assertEqual(jobflow.cv_reference(title).parent, jobflow.ROOT / "references")
        cfg = copy.deepcopy(self.cfg)
        cfg["cv_references"]["data-analyst"] = "custom-analyst.pdf"
        self.assertEqual(jobflow.cv_reference("Junior Data Analyst", cfg).name, "custom-analyst.pdf")
        self.assertEqual(jobflow.document_reference("letter", cfg=cfg).name, "motivation-letter.pdf")

    def test_general_cv_title_validation_and_slug(self):
        self.assertEqual(jobflow.validate_general_cv_title("  AI   Engineer (NLP)  "), "AI Engineer (NLP)")
        self.assertEqual(jobflow.general_cv_slug("AI Engineer (NLP)"), "ai-engineer-nlp")
        with self.assertRaisesRegex(SystemExit, "title must contain"):
            jobflow.validate_general_cv_title("Data Scientist; rm -rf")

    def test_professional_summary_bank_titles_only_reads_bank_headings(self):
        text = (Path(__file__).parents[1] / "master_cv.example.md").read_text()
        titles = jobflow.professional_summary_bank_titles(text)
        self.assertEqual(titles, ["Role One", "Role Two"])
        self.assertNotIn("Job Title — Employer", titles)
        self.assertNotIn("Professional Motivation", titles)

    def test_professional_summary_bank_titles_fails_closed(self):
        with self.assertRaisesRegex(SystemExit, "Professional Summary Bank not found"):
            jobflow.professional_summary_bank_titles("# Alex Example\n\n## Experience\n")
        with self.assertRaisesRegex(SystemExit, "contains no role headings"):
            jobflow.professional_summary_bank_titles("## Professional Summary Bank\n\nbody\n\n## Skills\n")

    def test_master_cv_uses_repo_file(self):
        self.assertEqual(jobflow.master_cv_path(), Path(jobflow.ROOT / "master_cv.md"))

    def test_human_readable_artifact_and_public_document_names(self):
        old = jobflow.ARTIFACTS
        with tempfile.TemporaryDirectory() as folder:
            jobflow.ARTIFACTS = Path(folder) / "artifacts"
            self.assertEqual(
                jobflow.job_artifact_folder("job/123", "ACME/Test", "Senior Data Scientist!").name,
                "acme-test_senior-data-scientist_job-123",
            )
            legacy = jobflow.ARTIFACTS / "job"
            legacy.mkdir(parents=True)
            self.assertEqual(jobflow.job_artifact_folder("job", "Example", "Data Scientist"), legacy)
            with mock.patch.object(jobflow, "candidate_name", return_value="Alex Example"):
                self.assertEqual(
                    jobflow.public_document_name("letter", "ACME/Test", "Senior Data Scientist!", ".pdf"),
                    "Alex_Example_Motivation_Letter_ACME_Test_Senior_Data_Scientist.pdf",
                )
                self.assertEqual(
                    jobflow.public_general_cv_name("Senior Data Scientist!", ".pdf"),
                    "Alex_Example_CV_General_Senior_Data_Scientist.pdf",
                )
        jobflow.ARTIFACTS = old

    def test_general_cv_generation_promotes_stable_files_and_master_metadata(self):
        old = jobflow.ARTIFACTS
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.ARTIFACTS = root / "artifacts"
            master = root / "master.md"
            master.write_text("# Alex Example — Master CV Source\n\nnewest master CV")

            def run(command, **kwargs):
                output = Path(command[command.index("-o") + 1])
                staging = output.parent
                for name, content in (("cv.md", "draft"), ("cv.docx", "docx"), ("cv.pdf", "pdf")):
                    (staging / name).write_text(content)
                output.write_text(json.dumps({
                    "agent_role": "writer", "run_id": "run", "attempt": 1, "documents": ["cv.md"],
                }))
                return mock.Mock(returncode=0, stdout="", stderr="")

            check = {"passed": True, "one_page": True, "docx": "staged/cv.docx"}
            with mock.patch.dict(os.environ, {"CODEX_BIN": "/bin/codex"}), \
                 mock.patch.object(jobflow, "master_cv_path", return_value=master), \
                 mock.patch.object(jobflow.subprocess, "run", side_effect=run), \
                 mock.patch.object(jobflow, "general_cv_check", return_value=check), \
                 redirect_stdout(io.StringIO()):
                jobflow.generate_general_cv("Data Scientist")

            destination = jobflow.ARTIFACTS / "general-cv" / "data-scientist"
            metadata = json.loads((destination / "metadata.json").read_text())
            self.assertEqual(metadata["master_cv_sha256"], jobflow.source_digest(master))
            self.assertEqual(metadata["prompt_sha256"], jobflow.general_cv_prompt_digest())
            self.assertEqual(metadata["check"]["docx"], str(destination / "cv.docx"))
            self.assertEqual((destination / "cv.pdf").read_text(), "pdf")
            self.assertEqual(
                metadata["documents"]["pdf"],
                str(destination / "Alex_Example_CV_General_Data_Scientist.pdf"),
            )
            self.assertEqual((destination / "Alex_Example_CV_General_Data_Scientist.pdf").read_text(), "pdf")
        jobflow.ARTIFACTS = old

    def test_general_cv_generation_retries_until_checks_pass(self):
        old = jobflow.ARTIFACTS
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.ARTIFACTS = root / "artifacts"
            master = root / "master.md"
            master.write_text("# Alex Example — Master CV Source\n\nnewest master CV")

            def run(command, **kwargs):
                output = Path(command[command.index("-o") + 1])
                attempt = int(output.stem.split("-")[-1])
                (output.parent / "cv.md").write_text(f"draft {attempt}")
                (output.parent / "cv.pdf").write_text(f"pdf {attempt}")
                output.write_text(json.dumps({
                    "agent_role": "writer", "run_id": "run", "attempt": attempt, "documents": ["cv.md"],
                }))
                return mock.Mock(returncode=0, stdout="", stderr="")

            checks = [
                {"passed": False, "preflight_failures": ["CV sections out of order"]},
                {"passed": True, "one_page": True},
            ]
            with mock.patch.dict(os.environ, {"CODEX_BIN": "/bin/codex"}), \
                 mock.patch.object(jobflow, "master_cv_path", return_value=master), \
                 mock.patch.object(jobflow, "config", return_value={**self.cfg, "max_revision_attempts": 2}), \
                 mock.patch.object(jobflow.subprocess, "run", side_effect=run) as runner, \
                 mock.patch.object(jobflow, "general_cv_check", side_effect=checks), \
                 redirect_stdout(io.StringIO()):
                jobflow.generate_general_cv("Data Scientist")

            destination = jobflow.ARTIFACTS / "general-cv" / "data-scientist"
            metadata = json.loads((destination / "metadata.json").read_text())
            self.assertEqual(metadata["status"], "PASS")
            self.assertEqual(metadata["attempts"], 2)
            self.assertEqual((destination / "cv.md").read_text(), "draft 2")
            self.assertEqual(runner.call_count, 2)
        jobflow.ARTIFACTS = old

    def test_general_cv_generation_retains_best_failed_draft(self):
        old = jobflow.ARTIFACTS
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.ARTIFACTS = root / "artifacts"
            master = root / "master.md"
            master.write_text("# Alex Example — Master CV Source\n\nnewest master CV")

            def run(command, **kwargs):
                output = Path(command[command.index("-o") + 1])
                (output.parent / "cv.md").write_text("best draft")
                output.write_text(json.dumps({
                    "agent_role": "writer", "run_id": "run", "attempt": 1, "documents": ["cv.md"],
                }))
                return mock.Mock(returncode=0, stdout="", stderr="")

            check = {"passed": False, "preflight_failures": ["CV sections out of order"]}
            with mock.patch.dict(os.environ, {"CODEX_BIN": "/bin/codex"}), \
                 mock.patch.object(jobflow, "master_cv_path", return_value=master), \
                 mock.patch.object(jobflow, "config", return_value={**self.cfg, "max_revision_attempts": 1}), \
                 mock.patch.object(jobflow.subprocess, "run", side_effect=run), \
                 mock.patch.object(jobflow, "general_cv_check", return_value=check), \
                 redirect_stdout(io.StringIO()):
                jobflow.generate_general_cv("Data Scientist")

            destination = jobflow.ARTIFACTS / "general-cv" / "data-scientist"
            metadata = json.loads((destination / "metadata.json").read_text())
            self.assertEqual(metadata["status"], "NEEDS REVIEW")
            self.assertEqual((destination / "cv.md").read_text(), "best draft")
            self.assertEqual(metadata["check"], check)
        jobflow.ARTIFACTS = old

    def test_general_cvs_batch_continues_and_summarizes(self):
        calls = []

        def generate(title, *, emit_output=True):
            calls.append(title)
            if title == "Data Engineer":
                raise SystemExit("boom")
            status = "NEEDS REVIEW" if title == "Data Analyst" else "PASS"
            return {
                "title": title, "status": status, "output": f"out/{jobflow.general_cv_slug(title)}",
                "attempts": 2,
                "check": {
                    "score": 100, "word_count": 403, "unsupported_numbers": [],
                    "reference_comparison": {"score_delta": 8},
                },
            }

        titles = ["Data Scientist", "Data Engineer", "Data Analyst"]
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder:
            master = Path(folder) / "master.md"
            master.write_text("master")
            with mock.patch.object(jobflow, "master_cv_path", return_value=master), \
                 mock.patch.object(jobflow, "master_professional_summary_titles", return_value=titles), \
                 mock.patch.object(jobflow, "generate_general_cv", side_effect=generate), \
                 redirect_stdout(output):
                jobflow.generate_general_cvs()

        summary = json.loads(output.getvalue())
        self.assertEqual(calls, titles)
        self.assertEqual((summary["titles"], summary["passed"], summary["needs_review"], summary["failed"]),
                         (3, 1, 1, 1))
        self.assertEqual(summary["results"][1], {
            "title": "Data Engineer", "status": "failed", "error": "boom",
        })
        self.assertEqual(summary["results"][0]["attempts"], 2)
        self.assertEqual(summary["results"][0]["score"], 100)
        self.assertEqual(summary["results"][0]["score_delta"], 8)
        self.assertEqual(summary["results"][0]["word_count"], 403)
        self.assertEqual(summary["results"][0]["unsupported_numbers"], 0)

    def test_general_cvs_skip_current_uses_matching_pass_metadata(self):
        old = jobflow.ARTIFACTS
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.ARTIFACTS = root / "artifacts"
            master = root / "master.md"
            master.write_text("master")
            slug = "data-scientist"
            destination = jobflow.ARTIFACTS / "general-cv" / slug
            destination.mkdir(parents=True)
            (destination / "metadata.json").write_text(json.dumps({
                "title": "Data Scientist", "slug": slug, "status": "PASS", "output": str(destination),
                "attempts": 3, "master_cv_sha256": jobflow.source_digest(master),
                "prompt_sha256": jobflow.general_cv_prompt_digest(),
                "check": {
                    "score": 100, "word_count": 400, "unsupported_numbers": [],
                    "reference_comparison": {"score_delta": 8},
                },
            }))
            output = io.StringIO()
            with mock.patch.object(jobflow, "master_cv_path", return_value=master), \
                 mock.patch.object(jobflow, "master_professional_summary_titles", return_value=["Data Scientist"]), \
                 mock.patch.object(jobflow, "generate_general_cv") as generate, \
                 redirect_stdout(output):
                jobflow.generate_general_cvs(skip_current=True)
            summary = json.loads(output.getvalue())
            generate.assert_not_called()
            self.assertEqual(summary["results"][0]["status"], "SKIPPED")
            self.assertEqual(summary["results"][0]["attempts"], 3)
            self.assertEqual(summary["results"][0]["score"], 100)
        jobflow.ARTIFACTS = old

    def test_general_cvs_skip_current_regenerates_stale_or_missing_metadata(self):
        old = jobflow.ARTIFACTS
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.ARTIFACTS = root / "artifacts"
            master = root / "master.md"
            master.write_text("master")
            current_master = jobflow.source_digest(master)
            current_prompt = jobflow.general_cv_prompt_digest()
            for title, metadata in {
                "Needs Review": {"status": "NEEDS REVIEW", "master_cv_sha256": current_master, "prompt_sha256": current_prompt},
                "Old Master": {"status": "PASS", "master_cv_sha256": "old", "prompt_sha256": current_prompt},
                "Old Prompt": {"status": "PASS", "master_cv_sha256": current_master, "prompt_sha256": "old"},
            }.items():
                slug = jobflow.general_cv_slug(title)
                destination = jobflow.ARTIFACTS / "general-cv" / slug
                destination.mkdir(parents=True)
                (destination / "metadata.json").write_text(json.dumps({
                    "title": title, "slug": slug, "attempts": 1, "check": {}, **metadata,
                }))

            calls = []

            def generate(title, *, emit_output=True):
                calls.append(title)
                return {"title": title, "status": "PASS", "output": f"out/{jobflow.general_cv_slug(title)}",
                        "attempts": 1, "check": {"unsupported_numbers": []}}

            titles = ["Missing", "Needs Review", "Old Master", "Old Prompt"]
            with mock.patch.object(jobflow, "master_cv_path", return_value=master), \
                 mock.patch.object(jobflow, "master_professional_summary_titles", return_value=titles), \
                 mock.patch.object(jobflow, "generate_general_cv", side_effect=generate), \
                 redirect_stdout(io.StringIO()):
                jobflow.generate_general_cvs(skip_current=True)
            self.assertEqual(calls, titles)
        jobflow.ARTIFACTS = old

    def test_pdf_comparison_fails_closed_when_input_is_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            with self.assertRaisesRegex(FileNotFoundError, "visual reference input missing"):
                jobflow.make_pdf_comparison(folder / "missing.pdf", folder / "reference.pdf",
                                            folder / "comparison.png")

    def test_prompt_contracts(self):
        prompts = Path(__file__).parents[1] / "prompts"
        automation = (Path(__file__).parents[1] / "AUTOMATION.md").read_text()
        commands = (Path(__file__).parents[1] / "COMMANDS.md").read_text()
        general_cv = (prompts / "general_cv.md").read_text()
        cv = (prompts / "tailor_cv.md").read_text()
        letter = (prompts / "tailor_letter.md").read_text()
        outreach = (prompts / "outreach.md").read_text()
        evaluate = (prompts / "evaluate.md").read_text()
        match = (prompts / "evaluate_job.md").read_text()
        pasted = (prompts / "start_from_given_description.md").read_text()
        telegram_summary = (prompts / "telegram_summary.md").read_text()

        self.assertIn("300–450 words", letter)
        self.assertIn("Hiring Team", letter)
        self.assertIn("Word-style DOCX/PDF renderer", letter)
        self.assertIn("reference-like density", cv)
        self.assertIn("optional Experience, optional Projects", cv)
        self.assertNotIn("unless Experience stronger", cv)
        preset_cv = (prompts / "presets" / "data_ai_general_cv.md").read_text()
        self.assertIn("AI / Machine Learning Engineer: `AI/ML`, `LLM/RAG`, `Engineering`, `Data`", preset_cv)
        self.assertIn("Data Scientist: `Programming`, `Machine Learning`, `Methods`, `Data and MLOps`, `Analytics`", preset_cv)
        self.assertIn("Avoid banned generic phrases", general_cv)
        self.assertIn("repeated bullet-opening verbs", cv + general_cv)
        self.assertIn("## `/general-cvs`", commands)
        self.assertIn("python jobflow.py general-cvs", commands)
        self.assertIn("CONTACT_CONTEXT", outreach)
        self.assertIn("under 140 words", outreach)
        self.assertIn("under 500 characters", outreach)
        self.assertIn("DOCUMENT_TYPE", evaluate)
        self.assertIn('`outreach`', evaluate)
        self.assertIn('"contact_issues":[]', evaluate)
        self.assertIn("APPLICATION_QUESTIONS", letter)
        self.assertIn("without numbered headings", letter)
        self.assertIn("Keep only the heading centered", letter)
        self.assertIn("Mention language fit only when", letter)
        self.assertIn("ai_tone_issues", evaluate)
        writer = (prompts / "write_documents.md").read_text()
        reviewer = (prompts / "review_documents.md").read_text()
        self.assertIn("writing-only", writer.lower())
        self.assertIn("Never score", writer)
        self.assertIn("review-only", reviewer)
        self.assertIn("Do not edit any file", reviewer)
        self.assertIn("writer reasoning", reviewer.lower())
        self.assertIn("score ≥90", reviewer)
        self.assertIn("Do not require 91", reviewer)
        for prompt_text in (cv, letter, outreach, evaluate, match, writer, reviewer, pasted, telegram_summary):
            self.assertNotRegex(prompt_text, r"(?m)^Skills:")
        self.assertIn("do not load external skills", writer.lower())
        self.assertIn("silent natural-language audit", writer)
        self.assertIn("clusters, not isolated words", evaluate)
        provenance = (Path(__file__).parents[1] / "PROMPT_PROVENANCE.md").read_text()
        self.assertIn("1b48564898e999219882660237fde01bf4843a0f", provenance)
        self.assertIn("Runtime prompts in this repository are self-contained", provenance)
        self.assertIn("Drafts only; nothing sent to recruiter.", telegram_summary)
        self.assertIn("85–89", telegram_summary)
        self.assertIn("care-needed disclaimer", telegram_summary)
        self.assertIn("Never say an application was applied", telegram_summary)
        self.assertIn("Do not decide whether delivery is allowed", telegram_summary)
        self.assertIn("`reject` below 50", match)
        self.assertIn("Classify every `###` role under `Professional Experience` exactly once", match)
        self.assertIn("project_assessment", match)
        self.assertIn("Python calculates dates and totals", match)
        self.assertIn("count_status` controls required-year credit, not visibility", cv)
        self.assertIn("Omit unrelated items", cv)
        self.assertIn("omit job/work location", cv)
        self.assertIn("no location, relocation, availability, or start date", cv)
        self.assertIn("visible application context", cv)
        self.assertIn("thesis/end-project as a bullet directly below", cv)
        self.assertIn("one A4 page", cv)
        self.assertIn("Never apply", automation)
        self.assertIn("natural professional English", automation)
        self.assertIn("--documents cv", automation)
        self.assertIn("mark-needs-review", automation)
        commands = (Path(__file__).parents[1] / "COMMANDS.md").read_text()
        agents = (Path(__file__).parents[1] / "AGENTS.md").read_text()
        readme = (Path(__file__).parents[1] / "README.md").read_text()
        for command in re.findall(r"^## `(/[^`]+)`", commands, re.M):
            self.assertIn(command, agents)
            self.assertIn(command, readme)
        marketplace_prompt = prompts / "discover_marketplaces_with_plugins.md"
        runtime_prompts = [Path(__file__).parents[1] / "AUTOMATION.md",
                           *(path for path in prompts.glob("*.md") if path != marketplace_prompt)]
        self.assertLessEqual(sum(path.stat().st_size for path in runtime_prompts), 24_000)
        self.assertLessEqual(marketplace_prompt.stat().st_size, 1_500)
        json.loads((Path(__file__).parents[1] / "agent_brief.schema.json").read_text())
        json.loads((Path(__file__).parents[1] / "agent_run.schema.json").read_text())
        json.loads((Path(__file__).parents[1] / "general_cv_result.schema.json").read_text())

    def test_match_threshold_and_artifacts(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         ("job1", "Example", "Data Scientist", "Netherlands", "https://example.test/1",
                          "Python role", "screening", 60, "[]", "2026-01-01"))
            conn.commit()
            conn.close()
            result = {"score": 50, "components": {"required_skills": 15, "responsibilities": 10,
                "seniority_experience": 8, "education_domain": 5, "ats_overlap": 8,
                "practical_constraints": 4}, "seniority": "entry", "responsibility_list": ["Build models"],
                "required_skill_list": ["Python"], "preferred_skill_list": [], "ats_keywords": ["Python"],
                "application_questions": [], "evidence_map": [{"requirement": "Python", "evidence": "Python"}],
                "missing_requirements": [], "job_summary": "Short summary.", **experience_fields()}
            path = root / "match.json"
            path.write_text(json.dumps(result))
            with mock.patch.object(jobflow, "master_cv", return_value=TEST_EXPERIENCE_CV):
                jobflow.record_match("job1", path)
            check = jobflow.db()
            self.assertEqual(check.execute("SELECT status FROM jobs").fetchone()[0], "accepted")
            check.close()
            artifact = jobflow.ARTIFACTS / "example_data-scientist_job1"
            self.assertTrue((artifact / "job.md").exists())
            brief = json.loads((artifact / "brief.json").read_text())
            self.assertEqual(brief["contacts"], [])
            self.assertEqual(brief["generation_constraints"]["cv_word_budget"], [0, 430])
            self.assertEqual(brief["source_item_counts"], {"experience": 1, "projects": 0})
            self.assertEqual(brief["project_assessment"], [])
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_relevant_experience_uses_elapsed_unique_months_and_caution(self):
        cv = """## Professional Experience

### Engineer — Company
City | Jan 2026 – Mar 2026
- Built data pipelines.

### Intern — Company
City | Mar 2026 – Present
- Tested data pipelines.

### Student Lead — University Team
City | Aug 2026 – Dec 2026
- Led a student prototype.

## Education
"""
        details = {
            "experience_requirement": {"kind": "mandatory", "minimum_months": 6, "wording": "six months"},
            "experience_roles": [
                {"role": "Engineer — Company", "experience_type": "professional_employment",
                 "relevance": "direct", "count_status": "confirmed", "evidence": ["Built data pipelines."],
                 "rationale": "Direct work."},
                {"role": "Intern — Company", "experience_type": "formal_internship",
                 "relevance": "direct", "count_status": "possible", "evidence": ["Tested data pipelines."],
                 "rationale": "Relevant formal internship; vacancy wording is unclear."},
                {"role": "Student Lead — University Team", "experience_type": "student_team",
                 "relevance": "direct", "count_status": "excluded", "evidence": ["Led a student prototype."],
                 "rationale": "Relevant evidence but not employment."},
            ],
        }
        result = jobflow.relevant_experience_assessment(details, cv, jobflow.date(2026, 7, 10))
        self.assertEqual((result["confirmed_months"], result["confirmed_or_possible_months"]), (3, 7))
        self.assertEqual(result["status"], "sufficient_with_caution")
        self.assertEqual(result["roles"][2]["elapsed_months"], 0)

    def test_empty_master_cv_work_sections_are_valid_zero_evidence(self):
        details = {"experience_requirement": {"kind": "mandatory", "minimum_months": 1,
                                               "wording": "one month"},
                   "experience_roles": [], "project_assessment": []}
        for document in ("# Master CV\n\n## Education\nDegree\n",
                         "## Professional Experience\n\n## Complete Project Bank\n\n## Education\nDegree\n"):
            with self.subTest(document=document):
                self.assertEqual(jobflow.professional_experience_roles(document), [])
                self.assertEqual(jobflow.master_cv_projects(document), [])
                assessment = jobflow.relevant_experience_assessment(details, document)
                self.assertEqual((assessment["confirmed_months"],
                                  assessment["confirmed_or_possible_months"], assessment["status"]),
                                 (0, 0, "insufficient"))
                self.assertEqual(jobflow.relevant_project_assessment(details, document), [])

        with self.assertRaisesRegex(SystemExit, "content but no ### roles"):
            jobflow.professional_experience_roles("## Professional Experience\nUnstructured work history")
        with self.assertRaisesRegex(SystemExit, "content but no ### projects"):
            jobflow.master_cv_projects("## Complete Project Bank\nUnstructured project history")

    def test_project_assessment_requires_exact_complete_project_inventory(self):
        master = ("## Complete Project Bank\n\n### Forecasting Platform\n\n"
                  "- Built Python forecasts.\n\n### Delivery Work\n\n- Delivered orders.\n")
        details = {"project_assessment": [
            {"project": "Forecasting Platform", "relevance": "direct",
             "evidence": ["Built Python forecasts."], "rationale": "Matches forecasting duties."},
            {"project": "Delivery Work", "relevance": "unrelated",
             "evidence": ["Delivered orders."], "rationale": "No vacancy-relevant evidence."},
        ]}
        self.assertEqual([item["relevance"] for item in
                          jobflow.relevant_project_assessment(details, master)], ["direct", "unrelated"])
        details["project_assessment"].pop()
        with self.assertRaisesRegex(SystemExit, "classify every"):
            jobflow.relevant_project_assessment(details, master)

    def test_relevant_experience_rejects_malformed_dates_and_invalid_counting(self):
        malformed = TEST_EXPERIENCE_CV.replace("Jan 2020 – Dec 2020", "during 2020")
        with self.assertRaisesRegex(SystemExit, "experience date must use"):
            jobflow.professional_experience_roles(malformed)

        cases = (
            ("student_team", "direct", "confirmed", "student-team experience cannot count"),
            ("volunteering", "direct", "confirmed", "non-professional experience cannot be confirmed"),
            ("professional_employment", "supporting", "confirmed", "only directly relevant roles may count"),
        )
        for experience_type, relevance, count_status, error in cases:
            fields = experience_fields("mandatory", 1, count_status)
            fields["experience_roles"][0].update({"experience_type": experience_type, "relevance": relevance})
            with self.subTest(experience_type=experience_type), self.assertRaisesRegex(SystemExit, error):
                jobflow.relevant_experience_assessment(fields, TEST_EXPERIENCE_CV)

        valid = (
            ("formal_internship", "direct", "confirmed", 12),
            ("academic_employment", "direct", "confirmed", 12),
            ("academic_employment", "supporting", "excluded", 0),
            ("volunteering", "direct", "possible", 12),
            ("professional_employment", "unrelated", "excluded", 0),
        )
        for experience_type, relevance, count_status, expected in valid:
            fields = experience_fields("mandatory", 1, count_status)
            fields["experience_roles"][0].update({"experience_type": experience_type, "relevance": relevance})
            result = jobflow.relevant_experience_assessment(fields, TEST_EXPERIENCE_CV)
            with self.subTest(experience_type=experience_type, relevance=relevance):
                self.assertEqual(result["confirmed_or_possible_months"], expected)

    def test_record_match_gates_mandatory_experience_and_surfaces_caution(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            for jid in ("short", "cautious", "preferred", "ambiguous"):
                conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?)", (jid, "Example", "Data Analyst", "Netherlands",
                              f"https://example.test/{jid}", "Python role", "screening", 80, "[]", "2026-01-01"))
            conn.commit(); conn.close()
            base = {"score": 80, "components": {"required_skills": 25, "responsibilities": 20,
                "seniority_experience": 10, "education_domain": 8, "ats_overlap": 12,
                "practical_constraints": 5}, "seniority": "entry", "responsibility_list": ["Build models"],
                "required_skill_list": ["Python"], "preferred_skill_list": [], "ats_keywords": ["Python"],
                "application_questions": [], "evidence_map": [], "missing_requirements": [], "job_summary": "Summary."}
            with mock.patch.object(jobflow, "master_cv", return_value=TEST_EXPERIENCE_CV):
                path = root / "match.json"
                path.write_text(json.dumps({**base, **experience_fields("mandatory", 1, "excluded")}))
                jobflow.record_match("short", path)
                path.write_text(json.dumps({**base, **experience_fields("mandatory", 12, "possible")}))
                jobflow.record_match("cautious", path)
                path.write_text(json.dumps({**base, **experience_fields("preferred", 13, "excluded")}))
                jobflow.record_match("preferred", path)
                path.write_text(json.dumps({**base, **experience_fields("ambiguous", 13, "excluded")}))
                jobflow.record_match("ambiguous", path)
            check = jobflow.db()
            self.assertEqual(check.execute("SELECT status FROM jobs WHERE id='short'").fetchone()[0], "rejected")
            self.assertEqual({row[0] for row in check.execute(
                "SELECT status FROM jobs WHERE id IN ('cautious','preferred','ambiguous')")}, {"accepted"})
            check.close()
            brief = json.loads((jobflow.ARTIFACTS / "example_data-analyst_cautious" / "brief.json").read_text())
            self.assertEqual(brief["experience_assessment"]["status"], "sufficient_with_caution")
            self.assertTrue(any("possible evidence" in item for item in brief["verification_needed"]))
            preferred = json.loads((jobflow.ARTIFACTS / "example_data-analyst_preferred" / "brief.json").read_text())
            ambiguous = json.loads((jobflow.ARTIFACTS / "example_data-analyst_ambiguous" / "brief.json").read_text())
            self.assertTrue(any("Relevant experience shortfall" in item for item in preferred["gaps"]))
            self.assertTrue(any("Relevant experience shortfall" in item for item in ambiguous["verification_needed"]))
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_record_match_zero_experience_rejects_mandatory_but_generates_nonmandatory_brief(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            for jid in ("mandatory", "optional"):
                conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?)", (jid, "Example", "Data Analyst", "Netherlands",
                             f"https://example.test/{jid}", "Python role", "screening", 80, "[]", "2026-01-01"))
            conn.commit(); conn.close()
            base = {"score": 80, "components": {"required_skills": 25, "responsibilities": 20,
                    "seniority_experience": 10, "education_domain": 8, "ats_overlap": 12,
                    "practical_constraints": 5}, "seniority": "entry", "responsibility_list": ["Build models"],
                    "required_skill_list": ["Python"], "preferred_skill_list": [], "ats_keywords": ["Python"],
                    "application_questions": [], "evidence_map": [], "missing_requirements": [],
                    "job_summary": "Summary.", "experience_roles": [], "project_assessment": []}
            path = root / "match.json"
            with mock.patch.object(jobflow, "master_cv", return_value="## Education\nDegree\n"):
                path.write_text(json.dumps({**base, "experience_requirement": {
                    "kind": "mandatory", "minimum_months": 1, "wording": "one month"}}))
                jobflow.record_match("mandatory", path)
                path.write_text(json.dumps({**base, "experience_requirement": {
                    "kind": "none", "minimum_months": 0, "wording": ""}}))
                jobflow.record_match("optional", path)
            check = jobflow.db()
            self.assertEqual(check.execute("SELECT status FROM jobs WHERE id='mandatory'").fetchone()[0], "rejected")
            self.assertEqual(check.execute("SELECT status FROM jobs WHERE id='optional'").fetchone()[0], "accepted")
            check.close()
            brief = json.loads((jobflow.ARTIFACTS / "example_data-analyst_optional" / "brief.json").read_text())
            self.assertEqual(brief["source_item_counts"], {"experience": 0, "projects": 0})
            self.assertEqual(brief["generation_constraints"]["required_cv_sections"],
                             ["Summary", "Education", "Skills", "Languages"])
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_record_match_rejects_non_entry_seniority(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         ("job1", "Example", "Data Modeler", "Netherlands", "https://example.test/1",
                          "Python role", "screening", 60, "[]", "2026-01-01"))
            conn.commit()
            conn.close()
            result = {"score": 80, "components": {"required_skills": 25, "responsibilities": 20,
                "seniority_experience": 10, "education_domain": 8, "ats_overlap": 12,
                "practical_constraints": 5}, "seniority": "medior", "responsibility_list": ["Build models"],
                "required_skill_list": ["Python"], "preferred_skill_list": [], "ats_keywords": ["Python"],
                "application_questions": [], "evidence_map": [{"requirement": "Python", "evidence": "Python"}],
                "missing_requirements": [], "job_summary": "Short summary.", **experience_fields()}
            path = root / "match.json"
            path.write_text(json.dumps(result))
            with mock.patch.object(jobflow, "master_cv", return_value=TEST_EXPERIENCE_CV):
                jobflow.record_match("job1", path)
            check = jobflow.db()
            self.assertEqual(check.execute("SELECT status FROM jobs").fetchone()[0], "rejected")
            self.assertFalse((jobflow.ARTIFACTS / "example_data-modeler_job1" / "brief.json").exists())
            check.close()
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_contact_extraction_uses_explicit_official_links_only(self):
        contacts = jobflow.extract_contacts(
            '<a href="mailto:jobs@example.com">Recruiter</a>'
            '<a href="https://linkedin.com/in/recruiter">Profile</a>'
            '<a href="https://linkedin.com/company/example">Company</a>',
            "https://example.com/job",
        )
        self.assertEqual([item["type"] for item in contacts], ["email", "linkedin"])
        self.assertTrue(all(item["verified"] for item in contacts))

    def test_lever_api_fast_path(self):
        response = mock.Mock()
        response.json.return_value = [{"text": "Data Scientist", "descriptionPlain": "Build models",
            "additionalPlain": "Python required", "hostedUrl": "https://jobs.lever.co/example/1",
            "categories": {"location": "Amsterdam", "commitment": "Full-time"},
            "lists": [{"text": "Requirements", "content": (
                "<li>Security clearance or the ability to obtain a clearance.</li>"
                "<li>Dutch language fluency.</li>"
            )}]}]
        with mock.patch.object(jobflow.requests, "get", return_value=response) as get:
            jobs, complete = jobflow.ats_jobs("Example", "https://jobs.lever.co/example", 60)
        self.assertTrue(complete)
        self.assertEqual((jobs[0]["title"], jobs[0]["employment_type"]), ("Data Scientist", "Full-time"))
        self.assertIn("Requirements", jobs[0]["description"])
        self.assertIn("Security clearance or the ability to obtain a clearance.", jobs[0]["description"])
        self.assertIn("Dutch language fluency.", jobs[0]["description"])
        accepted, reasons, _ = jobflow.filter_job(jobs[0], {"example"}, self.cfg, self.cv)
        self.assertFalse(accepted)
        self.assertTrue(any("Dutch" in reason for reason in reasons), reasons)
        self.assertTrue(any("screening" in reason for reason in reasons), reasons)
        self.assertIn("api.lever.co", get.call_args.args[0])

    def test_workday_skips_broken_posting_details(self):
        listing = mock.Mock()
        listing.json.return_value = {"total": 2, "jobPostings": [
            {"externalPath": "/job/broken", "title": "Broken"},
            {"externalPath": "/job/good", "title": "Data Analyst", "locationsText": "Eindhoven"},
        ]}
        broken = mock.Mock()
        broken.raise_for_status.side_effect = requests.HTTPError("gone")
        good = mock.Mock()
        good.json.return_value = {"jobPostingInfo": {
            "title": "Data Analyst", "location": "Eindhoven", "jobDescription": "Python",
            "timeType": "Full time", "externalUrl": "https://nxp.example/good",
        }}
        with mock.patch.object(jobflow.requests, "post", return_value=listing), \
             mock.patch.object(jobflow.requests, "get", side_effect=[broken, good]):
            jobs, complete = jobflow.ats_jobs("NXP", "https://nxp.wd3.myworkdayjobs.com/careers", 60)
        self.assertTrue(complete)
        self.assertEqual([job["title"] for job in jobs], ["Data Analyst"])

    def test_workday_selects_netherlands_country_facet(self):
        initial = mock.Mock()
        initial.json.return_value = {"total": 100, "jobPostings": [], "facets": [{
            "facetParameter": "Location_Country", "values": [
                {"descriptor": "Netherlands", "id": "nl"}, {"descriptor": "India", "id": "in"},
            ]}]}
        filtered = mock.Mock()
        filtered.json.return_value = {"total": 1, "jobPostings": [
            {"externalPath": "/job/nl", "title": "Data Analyst", "locationsText": "Eindhoven"}]}
        detail = mock.Mock()
        detail.json.return_value = {"jobPostingInfo": {"jobDescription": "Python", "externalUrl": "https://x/job/nl"}}
        with mock.patch.object(jobflow.requests, "post", side_effect=[initial, filtered]) as post, \
             mock.patch.object(jobflow.requests, "get", return_value=detail):
            jobs, complete = jobflow.ats_jobs("NXP", "https://nxp.wd3.myworkdayjobs.com/careers", 60)
        self.assertTrue(complete)
        self.assertEqual(jobs[0]["location"], "Eindhoven")
        self.assertEqual(post.call_args_list[1].kwargs["json"]["appliedFacets"], {"Location_Country": ["nl"]})

    def test_workday_fails_when_every_detail_fails(self):
        listing = mock.Mock()
        listing.json.return_value = {"total": 1, "jobPostings": [{"externalPath": "/job/broken"}]}
        broken = mock.Mock()
        broken.raise_for_status.side_effect = requests.HTTPError("gone")
        with mock.patch.object(jobflow.requests, "post", return_value=listing), \
             mock.patch.object(jobflow.requests, "get", return_value=broken):
            with self.assertRaisesRegex(RuntimeError, "all Workday"):
                jobflow.ats_jobs("NXP", "https://nxp.wd3.myworkdayjobs.com/careers", 60)

    def test_known_ats_error_does_not_fall_back_to_html(self):
        with mock.patch.object(jobflow.requests, "post", side_effect=requests.Timeout("slow")):
            with self.assertRaises(requests.Timeout):
                jobflow.scrape_source("NXP", "https://nxp.wd3.myworkdayjobs.com/careers")

    def test_manual_lead_import_filters_and_deduplicates(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA = Path(folder)
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO sponsors VALUES ('example tech','Example Tech B.V.',NULL,'now')")
            conn.commit(); conn.close()
            vacancy = json.dumps({"@type": "JobPosting", "title": "Junior Data Scientist",
                "hiringOrganization": {"name": "Example Tech B.V."}, "jobLocation": "Netherlands",
                "description": "Python SQL machine learning analytics NLP. One year experience preferred.",
                "url": "https://company.example/jobs/1"})
            page = f'<script type="application/ld+json">{vacancy}</script>'
            with mock.patch.object(jobflow, "fetch", return_value=page), \
                 mock.patch.object(jobflow, "master_cv", return_value=self.cv):
                jobflow.add_lead("linkedin", "https://company.example/jobs/1")
                jobflow.add_lead("indeed", "https://company.example/jobs/1")
            check = jobflow.db()
            self.assertEqual(check.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
            statuses = [row[0] for row in check.execute("SELECT status FROM lead_imports ORDER BY id")]
            self.assertEqual(statuses, ["eligible", "duplicate"])
            check.close()
        jobflow.DATA, jobflow.DB_PATH = old

    def test_manual_lead_rejects_aggregator_url(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA = Path(folder)
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            with self.assertRaisesRegex(SystemExit, "official employer"):
                jobflow.add_lead("linkedin", "https://www.linkedin.com/jobs/view/1")
            check = jobflow.db()
            self.assertEqual(check.execute("SELECT status FROM lead_imports").fetchone()[0], "rejected")
            check.close()
        jobflow.DATA, jobflow.DB_PATH = old

    def test_import_jd_creates_screening_job_with_synthetic_url(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA = Path(folder)
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO sponsors VALUES ('example tech','Example Tech B.V.',NULL,'now')")
            conn.commit(); conn.close()
            output = io.StringIO()
            with mock.patch.object(jobflow, "master_cv", return_value=self.cv), redirect_stdout(output):
                jobflow.import_jd(
                    "Example Tech B.V.", "Junior Data Scientist", "Amsterdam, Netherlands",
                    "Use Python SQL machine learning analytics NLP. One year experience preferred.",
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "screening")
            self.assertTrue(payload["url"].startswith("manual://"))
            self.assertTrue((jobflow.DATA / "screening" / f"{payload['job_id']}.json").exists())
            check = jobflow.db()
            row = check.execute("SELECT discovery_source,status FROM jobs").fetchone()
            self.assertEqual(tuple(row), ("manual", "screening"))
            check.close()
        jobflow.DATA, jobflow.DB_PATH = old

    def test_import_jd_review_anyway_bypasses_relevance(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA = Path(folder)
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO sponsors VALUES ('example tech','Example Tech B.V.',NULL,'now')")
            conn.commit(); conn.close()
            output = io.StringIO()
            with mock.patch.object(jobflow, "master_cv", return_value=self.cv), redirect_stdout(output):
                jobflow.import_jd(
                    "Example Tech B.V.", "Risk Traineeship", "Amsterdam, Netherlands",
                    "Rotate through finance teams and stakeholder meetings.",
                    review_anyway=True,
                )
            self.assertEqual(json.loads(output.getvalue())["status"], "screening")
        jobflow.DATA, jobflow.DB_PATH = old

    def test_import_jd_rejects_before_screening_and_deduplicates(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA = Path(folder)
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO sponsors VALUES ('example tech','Example Tech B.V.',NULL,'now')")
            conn.commit(); conn.close()
            with mock.patch.object(jobflow, "master_cv", return_value=self.cv):
                output = io.StringIO()
                with redirect_stdout(output):
                    jobflow.import_jd(
                        "Example Tech B.V.", "Senior Data Scientist", "Amsterdam, Netherlands",
                        "Use Python SQL machine learning analytics NLP.", "https://example.test/manual",
                    )
                rejected = json.loads(output.getvalue())
                self.assertEqual(rejected["status"], "rejected")
                self.assertFalse((jobflow.DATA / "screening" / f"{rejected['job_id']}.json").exists())
                output = io.StringIO()
                with redirect_stdout(output):
                    jobflow.import_jd(
                        "Example Tech B.V.", "Junior Data Scientist", "Amsterdam, Netherlands",
                        "Use Python SQL machine learning analytics NLP.", "https://example.test/duplicate",
                    )
                first = json.loads(output.getvalue())
                output = io.StringIO()
                with redirect_stdout(output):
                    jobflow.import_jd(
                        "Example Tech B.V.", "Junior Data Scientist", "Amsterdam, Netherlands",
                        "Use Python SQL machine learning analytics NLP.", "https://example.test/duplicate",
                    )
                second = json.loads(output.getvalue())
            self.assertEqual((first["status"], second["status"]), ("screening", "duplicate"))
            check = jobflow.db()
            self.assertEqual(check.execute("SELECT COUNT(*) FROM jobs WHERE url='https://example.test/duplicate'").fetchone()[0], 1)
            check.close()
        jobflow.DATA, jobflow.DB_PATH = old

    def test_rescreen_dry_run_reports_newly_passing_rejected_jobs(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA = Path(folder)
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO sponsors VALUES ('example tech','Example Tech B.V.',NULL,'now')")
            job = self.job(url="https://example.test/rejected")
            conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?)",
                         ("job1", job["company"], job["title"], job["location"], job["url"],
                          job["description"], "rejected", 0, '["old gate"]', "2026-01-01"))
            conn.commit(); conn.close()
            output = io.StringIO()
            with mock.patch.object(jobflow, "master_cv", return_value=self.cv), redirect_stdout(output):
                jobflow.rescreen(False)
            payload = json.loads(output.getvalue())
            self.assertEqual((payload["mode"], payload["passing"], payload["jobs"][0]["id"]), ("dry-run", 1, "job1"))
            check = jobflow.db()
            self.assertEqual(check.execute("SELECT status FROM jobs WHERE id='job1'").fetchone()[0], "rejected")
            check.close()
        jobflow.DATA, jobflow.DB_PATH = old

    def test_document_preflight_catches_cheap_failures(self):
        cv_failures = jobflow.document_preflight("# Summary\n- Same\n- Same", "cv", {})
        self.assertTrue(any("missing CV sections" in item for item in cv_failures))
        self.assertIn("duplicate CV bullets", cv_failures)
        format_failures = jobflow.document_preflight(
            "# Alex Example\n\n## Summary\nx\n## Experience\nx\n## Projects\nx\n"
            "## Education\n*MSc Data Science and Artificial Intelligence | 2023 - 2025*\nTU/e\n"
            "*BSc Data Science | 2020 - 2023*\nTU/e and Tilburg\n"
            "## Skills\nPython, SQL, pandas\n## Languages\nEnglish - Fluent",
            "cv", {})
        self.assertIn("MSc thesis must be a bullet directly below education item", format_failures)
        self.assertIn("BSc end project must be a bullet directly below education item", format_failures)
        self.assertIn("CV skills must use categorized lines with boldable labels", format_failures)
        self.assertIn("CV languages must use comma-separated Language (Level) format", format_failures)
        prose_failures = jobflow.document_preflight(
            "# Alex Example\n\n## Summary\nresults-driven analyst\n## Experience\n*Role | 2024 - 2025*\nOrg\n"
            "- Responsible for reports\n- Responsible for dashboards\n"
            "## Projects\nx\n## Education\nx\n## Skills\n**Programming:** Python\n**Analytics:** SQL\n"
            "## Languages\nEnglish (Fluent)",
            "cv", {})
        self.assertIn("CV banned generic phrases: results-driven", prose_failures)
        self.assertIn("CV weak responsibility phrasing: responsible for", prose_failures)
        self.assertIn("CV repeated bullet opening verbs: Role | 2024 - 2025: responsible", prose_failures)
        extraction_failures = jobflow.document_preflight(
            "# Alex Example\n\n## Summary\nimbalanced-classification work\n## Experience\nx\n## Projects\nx\n"
            "## Education\nx\n## Skills\n**Programming:** Python\n**Analytics:** SQL\n"
            "## Languages\nEnglish (Fluent)",
            "cv", {})
        self.assertIn("CV extraction-risk phrase: use imbalanced classification", extraction_failures)
        order_failures = jobflow.document_preflight(
            "# Alex Example\n\n## Summary\nx\n## Projects\nx\n## Experience\nx\n"
            "## Education\nx\n## Skills\n**Programming:** Python\n**Analytics:** SQL\n"
            "## Languages\nEnglish (Fluent)",
            "cv", {})
        self.assertIn(
            "CV sections out of order: expected Summary, Experience, Projects, Education, Skills, Languages",
            order_failures)
        bullet_failures = jobflow.document_preflight(
            "# Alex Example\n\n## Summary\nx\n## Experience\n- " + ("long " * 30) +
            "\n## Projects\nx\n## Education\nx\n## Skills\n**Programming:** Python\n**Analytics:** SQL\n"
            "## Languages\nEnglish (Fluent)",
            "cv", {})
        self.assertIn("CV bullet exceeds one-line limit (1)", bullet_failures)
        crowded_failures = jobflow.document_preflight(
            "# Alex Example\n\n## Summary\n" + ("summary " * 45) +
            "\n## Experience\n*Role | 2024 - 2025*\nOrg\n- one\n- two\n- three\n- four\n- five\n"
            "## Projects\nx\n## Education\nx\n## Skills\n**Programming:** Python\n**Analytics:** SQL\n"
            "## Languages\nEnglish (Fluent)",
            "cv", {})
        self.assertIn("CV summary must fit within three rendered lines", crowded_failures)
        self.assertIn("CV experience/project items must have at most four bullets", crowded_failures)
        project_date_failures = jobflow.document_preflight(
            "# Alex Example\n\n## Summary\nx\n## Experience\n*Role | 2024 - 2025*\nOrg\n"
            "## Projects\n*Fraud Detection MLOps Pipeline | 2025*\n- Built model monitoring.\n"
            "## Education\nx\n## Skills\n**Programming:** Python\n**Analytics:** SQL\n"
            "## Languages\nEnglish (Fluent)",
            "cv", {})
        self.assertIn("CV project items must not show years/dates: Fraud Detection MLOps Pipeline | 2025",
                      project_date_failures)
        heading_failures = jobflow.document_preflight(
            "# Alex Example\n## Summary\nx\n## Experience\n### Role\n## Projects\nx\n"
            "## Education\nx\n## Skills\n**Programming:** Python\n**Analytics:** SQL\n"
            "## Languages\nEnglish (Fluent)", "cv", {})
        self.assertIn("CV item headings must use single-asterisk item lines, not ### headings", heading_failures)
        letter_failures = jobflow.document_preflight(
            "short letter", "letter", {"application_questions": ["Why?"]})
        self.assertTrue(any("word count" in item for item in letter_failures))
        self.assertNotIn("application question 1 not labelled", letter_failures)
        body = " ".join(["evidence"] * 290)
        with mock.patch.object(jobflow, "candidate_name", return_value="Alex Example"):
            valid_letter = jobflow.document_preflight(
                f"# Alex Example\nalex@example.com | +31 6 00000000 | Rotterdam, Netherlands\n\n"
                f"Dear Hiring Team,\n\n{body}\n\nKind regards,\n\nAlex Example", "letter", {})
            invalid_letter = jobflow.document_preflight(
                f"# Motivation Letter\nAlex Example\nExample B.V.\nDear Hiring Team,\n{body}", "letter", {})
        self.assertFalse(valid_letter)
        self.assertIn("letter must start with '# Candidate Name', one 'email | phone | location' line, then greeting",
                      invalid_letter)

    def test_document_preflight_allows_optional_work_sections_but_not_empty_headings(self):
        core = ("# Alex Example\n\n## Summary\nPython analyst.\n\n## Education\nCertificate\n\n"
                "## Skills\n**Programming:** Python\n**Analytics:** SQL\n\n"
                "## Languages\nEnglish (Fluent)")
        brief = {"source_item_counts": {"experience": 0, "projects": 0},
                 "generation_constraints": {
                     "required_cv_sections": ["Summary", "Education", "Skills", "Languages"]}}
        self.assertFalse(jobflow.document_preflight(core, "cv", brief, self.cfg))

        empty_experience = core.replace("## Education", "## Experience\n\n## Education")
        failures = jobflow.document_preflight(empty_experience, "cv", brief, self.cfg)
        self.assertIn("CV Experience section must contain at least one formatted item or be omitted", failures)
        self.assertIn("CV Experience section must be omitted because the master CV has no source items", failures)

        mixed = core.replace("## Education", "## Projects\n*Forecasting Platform*\n- Built forecasts.\n\n## Education")
        available = {**brief, "source_item_counts": {"experience": 0, "projects": 1}}
        self.assertFalse(jobflow.document_preflight(mixed, "cv", available, self.cfg))

    def test_document_preflight_accepts_prior_cv_format(self):
        cv = (
            "# Alex Example\n\n## Summary\nx\n## Experience\n*Analyst | 2024 - 2025*\nExample\n- Built reports.\n"
            "## Projects\n*Forecasting Platform*\n- Built forecasts.\n"
            "## Education\n*MSc Data Science and Artificial Intelligence | 2023 - 2025*\n"
            "Eindhoven University of Technology\n"
            "- Thesis: Reward-Free Safe Reinforcement Learning Exploration Using Different Entropy Measures\n"
            "*BSc Data Science | 2020 - 2023*\n"
            "Eindhoven University of Technology and Tilburg University\n"
            "- Bachelor End Project: Health Platform Text Classification Using Active Learning\n"
            "## Skills\n**Programming:** Python, SQL\n**Analytics:** process mining, Power BI\n"
            "## Languages\nEnglish (Fluent), Dutch (A2)\n"
        )
        self.assertFalse(jobflow.document_preflight(cv, "cv", {}))

    def test_pdf_layout_flags_joined_text_defects(self):
        calls = [
            mock.Mock(stdout="Pages: 1\n"),
            mock.Mock(stdout="safe RL and imbalancedclassification work"),
        ]
        with mock.patch.object(jobflow.subprocess, "run", side_effect=calls):
            layout = jobflow.pdf_layout(Path("cv.pdf"))
        self.assertEqual(layout["pdf_text_failures"], ["PDF text defect: imbalancedclassification"])

    def test_role_specific_cv_failures_check_skill_categories_and_bank_coverage(self):
        master = (
            "## Professional Summary Bank\n\n### Data Scientist\n\n"
            "Predictive modeling NLP time-series process mining reinforcement learning "
            "imbalanced classification dashboards recommendations validation workflows.\n\n"
            "## Skills\n"
        )
        weak = (
            "## Summary\nData Scientist.\n## Projects\n*X*\n- Built reports.\n"
            "## Skills\nProgramming: Python\n"
        )
        failures = jobflow.role_specific_cv_failures("Data Scientist", weak, master)
        self.assertTrue(any("CV missing role skill categories" in item for item in failures))
        self.assertTrue(any("CV lacks role-bank coverage" in item for item in failures))

    def test_general_cv_check_records_reference_comparison_and_blocks_missing_reference(self):
        master = (
            "## Professional Summary Bank\n### Data Scientist\nPredictive modeling.\n"
            "## Professional Experience\n### Junior AI Specialist — Example Analytics\n"
            "City | Jun 2026 – Sep 2026\n- Built modeling dashboards.\n"
            "## Complete Project Bank\n### Fraud Detection MLOps Pipeline - IEEE-CIS\n"
            "- Built predictive workflows.\n## Skills\nPython\n"
        )
        passing_cv = (
            "# Alex Example\n\n## Summary\nData Scientist with predictive modeling, NLP, time-series, process mining, "
            "reinforcement learning, imbalanced classification, dashboards, validation, and recommendations.\n"
            "## Experience\n*Junior AI Specialist | Jun 2026 - Sep 2026*\nExample Analytics\n"
            "- Built modeling dashboards.\n"
            "## Projects\n*Fraud Detection MLOps Pipeline - IEEE-CIS*\n"
            "- Built predictive workflows.\n- Trained XGBoost models.\n- Served FastAPI models.\n"
            "## Education\n*MSc Data Science and Artificial Intelligence | 2023 - 2025*\n"
            "Eindhoven University of Technology\n"
            "- Thesis: Reward-Free Safe Reinforcement Learning Exploration Using Different Entropy Measures\n"
            "*BSc Data Science | 2020 - 2023*\nEindhoven University of Technology and Tilburg University\n"
            "- Bachelor End Project: Health Platform Text Classification Using Active Learning\n"
            "## Skills\nProgramming: Python, SQL, R, C++, Java, Bash, pandas, NumPy, validation, dashboards, workflows, recommendations\n"
            "Machine Learning: scikit-learn, XGBoost, PyTorch, TensorFlow, Keras, predictive modeling, classification\n"
            "Methods: NLP, time-series, forecasting, reinforcement learning, imbalanced classification, process mining\n"
            "Data and MLOps: ETL, data quality, REST APIs, FastAPI, MLflow, model serving, monitoring, Docker\n"
            "Analytics: Power BI, Plotly, Dash, Streamlit, Matplotlib, Jupyter, stakeholder reporting\n"
            "Extra: " + ("predictive modeling validation dashboards recommendations workflows " * 29) + "\n"
            "## Languages\nEnglish (Fluent), Dutch (A2)\n"
        )
        master += passing_cv
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "cv.md").write_text(passing_cv)
            reference = folder / "reference.pdf"
            reference.write_bytes(b"pdf")
            with mock.patch.object(jobflow, "master_cv", return_value=master), \
                 mock.patch.object(jobflow, "render_pdf", return_value={
                     "pages": 1, "one_page": True, "text_words": 360, "renderer": "docx",
                     "docx": str(folder / "cv.docx"), "cached": False, "pdf_text_failures": []}), \
                 mock.patch.object(jobflow, "cv_reference", return_value=reference), \
                 mock.patch.object(jobflow, "make_pdf_comparison", side_effect=lambda _g, _r, dst: dst.write_bytes(b"png")), \
                 mock.patch.object(jobflow.subprocess, "run", return_value=mock.Mock(stdout="Data Scientist predictive modeling")):
                result = jobflow.general_cv_check("Data Scientist", folder)
            self.assertIn("visual_comparison", result)
            self.assertEqual(result["reference_comparison"]["reference"], str(reference))
            self.assertIn("score_delta", result["reference_comparison"])

        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "cv.md").write_text(passing_cv)
            missing = folder / "missing.pdf"
            with mock.patch.object(jobflow, "master_cv", return_value=master), \
                 mock.patch.object(jobflow, "render_pdf", return_value={
                     "pages": 1, "one_page": True, "text_words": 360, "renderer": "docx",
                     "docx": str(folder / "cv.docx"), "cached": False, "pdf_text_failures": []}), \
                 mock.patch.object(jobflow, "cv_reference", return_value=missing), \
                 mock.patch.object(jobflow, "make_pdf_comparison", side_effect=FileNotFoundError("missing")):
                result = jobflow.general_cv_check("Data Scientist", folder)
            self.assertFalse(result["passed"])
            self.assertIn("visual_error", result)

    def test_letter_markdown_parser_keeps_only_header_contact_centered(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "letter.md"
            path.write_text("# Alex Example\nemail | phone | location\n\nDear Hiring Team,\n\nBody paragraph.")
            blocks = jobflow.markdown_blocks(path, "letter")
        self.assertEqual(blocks["contacts"], ["email | phone | location"])
        self.assertEqual([item["text"] for item in blocks["sections"][0]["items"]],
                         ["Dear Hiring Team,", "Body paragraph."])

    def test_letter_markdown_parser_drops_profile_link_row(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "letter.md"
            path.write_text(
                "# Alex Example\nemail | phone | location\n"
                "linkedin.com/in/alex-example | github.com/alex-example\n\nDear Hiring Team,\n\nBody paragraph."
            )
            blocks = jobflow.markdown_blocks(path, "letter")
        self.assertEqual(blocks["contacts"], ["email | phone | location"])
        self.assertEqual([item["text"] for item in blocks["sections"][0]["items"]],
                         ["Dear Hiring Team,", "Body paragraph."])

    def test_letter_renderer_adds_paragraph_spacing_but_keeps_signature_together(self):
        blocks = {
            "title": "Alex Example",
            "contacts": ["email | phone | location"],
            "sections": [{"heading": "BODY", "items": [
                {"kind": "text", "text": "Dear Hiring Team,", "main": False},
                {"kind": "text", "text": "Body paragraph.", "main": False},
                {"kind": "text", "text": "Kind regards,", "main": False},
                {"kind": "text", "text": "Alex Example", "main": False},
            ]}],
        }
        document = jobflow.docx_document_xml(blocks, "letter")
        self.assertGreaterEqual(document.count('w:after="220"'), 3)
        salutation = document[document.index("Kind regards,") - 300:document.index("Kind regards,")]
        self.assertIn('w:after="20"', salutation)

    def test_cv_parser_removes_target_role_and_preserves_date_item_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cv.md"
            path.write_text(
                "# Alex Example — Data Scientist\nemail | phone | location\n\n"
                "## Experience\n\n### Junior AI Specialist | Jun 2026 – Sep 2026\nCompany\n"
            )
            blocks = jobflow.markdown_blocks(path, "cv")
            destination = path.with_suffix(".docx")
            jobflow.write_docx_from_markdown(path, destination, "cv")
            with zipfile.ZipFile(destination) as docx:
                document = docx.read("word/document.xml").decode()
        self.assertEqual(blocks["title"], "Alex Example")
        self.assertEqual(blocks["sections"][0]["items"][0], {
            "kind": "text", "text": "Junior AI Specialist | Jun 2026 – Sep 2026", "main": True})
        self.assertIn('<w:tab w:val="right"', document)
        self.assertNotIn("Data Scientist", document)

    def test_cv_organization_and_institution_lines_are_not_italic(self):
        blocks = {
            "title": "Alex Example",
            "contacts": [],
            "sections": [{"heading": "EXPERIENCE", "items": [
                {"kind": "text", "text": "Junior AI Specialist | Jun 2026 – Sep 2026", "main": True},
                {"kind": "text", "text": "Example Analytics", "main": False},
            ]}],
        }
        document = jobflow.docx_document_xml(blocks, "cv")
        role_paragraph, organization_paragraph = document.split("</w:p>")[2:4]
        self.assertIn("<w:i/>", role_paragraph)
        self.assertNotIn("<w:i/>", organization_paragraph)
        self.assertIn("Example Analytics", organization_paragraph)

    def test_cv_parser_moves_dates_from_location_line_to_item_title(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cv.md"
            path.write_text(
                "# Alex Example\nemail | phone\n\n"
                "## Experience\n"
                "### Junior AI Specialist - Example Analytics\n"
                "*Eindhoven, The Netherlands | Jun 2026 - Sep 2026*\n"
                "## Education\n"
                "### MSc Data Science and Artificial Intelligence\n"
                "*Eindhoven, The Netherlands | 2023 - 2025*\n"
            )
            blocks = jobflow.markdown_blocks(path, "cv")
            destination = path.with_suffix(".docx")
            jobflow.write_docx_from_markdown(path, destination, "cv")
            with zipfile.ZipFile(destination) as docx:
                document = docx.read("word/document.xml").decode()
        experience, education = (section["items"] for section in blocks["sections"])
        self.assertEqual(experience[0]["text"], "Junior AI Specialist - Example Analytics | Jun 2026 - Sep 2026")
        self.assertEqual(experience[1]["text"], "Eindhoven, The Netherlands")
        self.assertEqual(education[0]["text"], "MSc Data Science and Artificial Intelligence | 2023 - 2025")
        self.assertEqual(education[1]["text"], "Eindhoven, The Netherlands")
        self.assertIn('<w:tab w:val="right"', document)

    def test_letter_parser_ignores_legacy_div_alignment_markup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "letter.md"
            path.write_text(
                '<div align="center">\n\nAlex Example\nemail | phone | location\n\n</div>\n\nDear Hiring Team,\n'
            )
            with mock.patch.object(jobflow, "candidate_name", return_value="Alex Example"):
                blocks = jobflow.markdown_blocks(path, "letter")
        self.assertEqual(blocks["title"], "Alex Example")
        self.assertEqual(blocks["contacts"], ["email | phone | location"])
        self.assertEqual(blocks["sections"][0]["items"][0]["text"], "Dear Hiring Team,")

    def test_categorized_failures_detect_visible_stuffing(self):
        details = {"preflight_failures": [], "unsupported_numbers": [], "generic_phrases": []}
        failures = jobflow.categorized_failures(
            "# Summary\nApplication context: bank keyword list", "cv", details, {}, None)
        self.assertIn("visible keyword-stuffing", failures["tone_failures"][0])

    def test_concept_score_uses_supported_brief_concepts_not_incidental_words(self):
        brief = {
            "ats_keywords": ["Python", "fraud monitoring", "stakeholder reporting"],
            "required_skills": [], "preferred_skills": [], "responsibilities": [],
            "evidence_map": [
                {"requirement": "Python", "evidence": "Built Python services"},
                {"requirement": "fraud monitoring", "evidence": "Monitored fraud models"},
                {"requirement": "stakeholder reporting", "evidence": "Delivered stakeholder reports"},
            ],
        }
        master = "Python fraud monitoring stakeholder reporting"
        natural, natural_details = jobflow.keyword_score(
            "Built Python fraud monitoring and explained results through stakeholder reporting. " * 35,
            "about week application role", master, "letter", brief)
        incidental, incidental_details = jobflow.keyword_score(
            "about week application role " * 100, "about week application role", master, "letter", brief)
        self.assertEqual(natural_details["concept_coverage"], 1.0)
        self.assertEqual(incidental_details["concept_coverage"], 0.0)
        self.assertGreater(natural, incidental)

    def test_long_concept_requires_two_meaningful_terms(self):
        concept = {"label": "stakeholder reporting and recommendations", "tokens": [
            "stakeholder", "reporting", "recommendations"]}
        self.assertFalse(jobflow.concept_is_covered(concept, {"reporting"}))
        self.assertTrue(jobflow.concept_is_covered(concept, {"reporting", "stakeholder"}))

    def test_natural_writing_detector_requires_pattern_cluster(self):
        self.assertEqual(jobflow.natural_writing_failures(
            "Additionally, this vibrant result is pivotal. Let's dive into the details.", "letter"),
            ["cluster of generic AI vocabulary", "announces the writing instead of stating it"])
        self.assertEqual(jobflow.natural_writing_failures(
            "The project was crucial to the migration because it removed a manual step.", "letter"), [])

    def test_question_coverage_is_a_hard_gate(self):
        brief = {"application_questions": ["Which financial innovation inspires you?"]}
        failures = jobflow.question_coverage_failures("I enjoy building dashboards.", brief)
        self.assertEqual(failures, ["application question 1 lacks topical coverage"])
        details = {"pdf_pages": 1, "categorized_failures": {
            "truth_failures": [], "layout_failures": [], "ats_failures": [], "tone_failures": [],
            "contact_failures": [], "question_failures": failures,
        }}
        self.assertFalse(jobflow.quality_gates(95, details, {"ats_threshold": 90})["question_gate"])

    def test_quality_gates_split_ats_from_truth_and_tone(self):
        details = {"pdf_pages": 1, "categorized_failures": {
            "truth_failures": ["unsupported number"], "layout_failures": [],
            "ats_failures": [], "tone_failures": ["visible keyword-stuffing"],
            "contact_failures": [],
        }}
        gates = jobflow.quality_gates(95, details, {"ats_threshold": 90})
        self.assertTrue(gates["ats_gate"])
        self.assertFalse(gates["truth_gate"])
        self.assertFalse(gates["tone_gate"])
        threshold_gates = jobflow.quality_gates(90, details, {"ats_threshold": 90})
        self.assertTrue(threshold_gates["ats_gate"])

    def test_render_pdf_reuses_source_hash_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source, destination = folder / "cv.md", folder / "cv.pdf"
            source.write_text("# Summary\nText")

            def convert(docx, pdf):
                pdf.write_bytes(b"pdf")
                return True

            with mock.patch.object(jobflow, "write_docx_from_markdown",
                                   side_effect=lambda src, dst, kind: dst.write_bytes(b"docx")) as writer, \
                 mock.patch.object(jobflow, "convert_docx_with_libreoffice", side_effect=convert), \
                 mock.patch.object(jobflow, "pdf_layout",
                                   return_value={"pages": 1, "one_page": True, "text_words": 2}):
                first = jobflow.render_pdf(source, destination)
                second = jobflow.render_pdf(source, destination)
            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            self.assertEqual(writer.call_count, 1)

    def test_score_selects_document_and_reuses_unchanged_evaluation(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                         "VALUES ('job','Example','Data Scientist','NL','https://example.test','Python analytics',"
                         "'accepted',80,'[]','now')")
            conn.commit(); conn.close()
            artifact = jobflow.ARTIFACTS / "job"
            artifact.mkdir(parents=True)
            cv = "# Summary\nPython analytics\n# Experience\n- Built models\n# Projects\n- Fraud model\n" \
                 "# Education\nMSc\n# Skills\nPython\n# Languages\nEnglish\n" + ("evidence " * 350)
            (artifact / "cv.md").write_text(cv)
            (artifact / "letter.md").write_text("untouched")
            (artifact / "brief.json").write_text(json.dumps({
                "ats_keywords": ["Python analytics"], "required_skills": [], "preferred_skills": [],
                "responsibilities": [], "evidence_map": [{"requirement": "Python", "evidence": "Python"}],
                "application_questions": [],
            }))
            with mock.patch.object(jobflow, "master_cv", return_value="Python analytics evidence"), \
                 mock.patch.object(jobflow, "render_pdf", return_value={
                     "pages": 1, "one_page": True, "text_words": 360, "renderer": "docx",
                     "docx": str(artifact / "cv.docx"), "cached": False}), \
                 mock.patch.object(jobflow, "make_pdf_comparison"):
                with redirect_stdout(io.StringIO()):
                    jobflow.score("job", "cv")
                second_output = io.StringIO()
                with redirect_stdout(second_output):
                    jobflow.score("job", "cv")
            check = jobflow.db()
            self.assertEqual(check.execute("SELECT COUNT(*) FROM evaluations WHERE document='cv'").fetchone()[0], 1)
            self.assertEqual(check.execute("SELECT COUNT(*) FROM evaluations WHERE document='letter'").fetchone()[0], 0)
            check.close()
            self.assertTrue(json.loads(second_output.getvalue())["cv"]["reused_evaluation"])
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_score_does_not_reuse_an_old_scoring_version(self):
        digest = "abc"
        prior = {"source_sha256": digest, "scoring_version": "old"}
        self.assertNotEqual(prior["scoring_version"], jobflow.SCORING_VERSION)

    def test_score_records_letter_comparison_and_blocks_comparison_failure(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                         "VALUES ('job','Example','Data Scientist','NL','https://example.test','Python',"
                         "'accepted',80,'[]','now')")
            conn.commit(); conn.close()
            artifact = jobflow.ARTIFACTS / "job"; artifact.mkdir(parents=True)
            letter = ("# Alex Example\nalex@example.com | +31 6 00000000 | Rotterdam, Netherlands\n\n"
                      "Dear Hiring Team,\n\n" + " ".join(["Python"] * 290) +
                      "\n\nKind regards,\n\nAlex Example")
            (artifact / "letter.md").write_text(letter)
            (artifact / "brief.json").write_text(json.dumps({"application_questions": []}))
            reference = root / "reference.pdf"; reference.write_bytes(b"pdf")
            render = {"pages": 1, "one_page": True, "text_words": 305, "renderer": "docx",
                      "docx": str(artifact / "letter.docx"), "cached": False}
            compare = lambda _generated, _reference, destination, _kind: destination.write_bytes(b"png")
            with mock.patch.object(jobflow, "candidate_name", return_value="Alex Example"), \
                 mock.patch.object(jobflow, "master_cv", return_value=letter), \
                 mock.patch.object(jobflow, "render_pdf", return_value=render), \
                 mock.patch.object(jobflow, "document_reference", return_value=reference), \
                 mock.patch.object(jobflow, "make_pdf_comparison", side_effect=compare), \
                 redirect_stdout(io.StringIO()):
                jobflow.score("job", "letter")
            check = jobflow.db()
            first = json.loads(check.execute("SELECT details FROM evaluations WHERE attempt=1").fetchone()[0])
            self.assertEqual(Path(first["visual_comparison"]).name, "letter-comparison-1.png")
            self.assertTrue(Path(first["visual_comparison"]).is_file())
            check.close()

            (artifact / "letter.md").write_text(letter + "\n")
            with mock.patch.object(jobflow, "candidate_name", return_value="Alex Example"), \
                 mock.patch.object(jobflow, "master_cv", return_value=letter), \
                 mock.patch.object(jobflow, "render_pdf", return_value=render), \
                 mock.patch.object(jobflow, "document_reference", return_value=reference), \
                 mock.patch.object(jobflow, "make_pdf_comparison", side_effect=ValueError("not A4")), \
                 redirect_stdout(io.StringIO()):
                jobflow.score("job", "letter")
            check = jobflow.db()
            second = json.loads(check.execute("SELECT details FROM evaluations WHERE attempt=2").fetchone()[0])
            check.close()
            self.assertIn("not A4", second["visual_error"])
            self.assertTrue(any("visual reference comparison failed" in failure
                                for failure in second["categorized_failures"]["layout_failures"]))
            self.assertFalse(second["gates"]["layout_gate"])
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_score_skips_render_when_cheap_gate_fails(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                         "VALUES ('job','Example','Data Scientist','NL','https://example.test','Python',"
                         "'accepted',80,'[]','now')")
            conn.commit(); conn.close()
            artifact = jobflow.ARTIFACTS / "job"; artifact.mkdir(parents=True)
            (artifact / "letter.md").write_text("Too short.")
            (artifact / "brief.json").write_text(json.dumps({"application_questions": []}))
            with mock.patch.object(jobflow, "master_cv", return_value="Python"), \
                 mock.patch.object(jobflow, "render_pdf") as render, redirect_stdout(io.StringIO()):
                jobflow.score("job", "letter")
            render.assert_not_called()
            check = jobflow.db()
            details = json.loads(check.execute("SELECT details FROM evaluations").fetchone()[0])
            check.close()
            self.assertEqual(details["render_skipped"], "cheap pre-render gate failed")
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_mark_needs_review_retains_artifacts_and_records_blocker(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                         "VALUES ('job','Example','Data Scientist','NL','https://example.test','Python',"
                         "'accepted',80,'[]','now')")
            conn.execute("INSERT INTO evaluations VALUES ('job','cv',1,90,'{}','now')")
            conn.commit(); conn.close()
            artifact = jobflow.ARTIFACTS / "job"; artifact.mkdir(parents=True)
            (artifact / "cv.md").write_text("draft")
            with redirect_stdout(io.StringIO()):
                jobflow.mark_needs_review("job", "agent usage limit")
            quality = json.loads((artifact / "quality.json").read_text())
            self.assertEqual(quality["status"], "NEEDS REVIEW")
            self.assertEqual(quality["blocker"], "agent usage limit")
            self.assertIn("cv.md", quality["retained_artifacts"])
            check = jobflow.db()
            self.assertEqual(check.execute("SELECT status FROM jobs").fetchone()[0], "needs_review")
            check.close()
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_contacts_manual_url_uses_placeholder(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                         "VALUES ('manual','Example','Data Scientist','NL','manual://abc','Python','accepted',80,'[]','now')")
            conn.commit(); conn.close()
            artifact = jobflow.job_artifact_folder("manual", "Example", "Data Scientist")
            artifact.mkdir(parents=True)
            (artifact / "brief.json").write_text(json.dumps({"generation_constraints": {
                "cv_word_budget": [350, 430], "required_cv_sections": [
                    "Summary", "Experience", "Projects", "Education", "Skills", "Languages"]}}))
            with mock.patch.object(jobflow, "master_cv", return_value="## Education\nCertificate\n"):
                jobflow.collect_contacts("manual")
            contacts = json.loads((artifact / "contacts.json").read_text())
            self.assertEqual((contacts[0]["type"], contacts[0]["source"], contacts[0]["verified"]),
                             ("placeholder", "unavailable", False))
            brief = json.loads((artifact / "brief.json").read_text())
            self.assertEqual(brief["source_item_counts"], {"experience": 0, "projects": 0})
            self.assertEqual(brief["generation_constraints"]["cv_word_budget"], [0, 430])
            self.assertEqual(brief["generation_constraints"]["required_cv_sections"],
                             ["Summary", "Education", "Skills", "Languages"])
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_accepts_zero_to_two_years_traineeship_range(self):
        accepted, reasons, _ = jobflow.filter_job(
            self.job(description="Approximately 0 to 2 years of working experience. Python SQL data analytics NLP."),
            self.sponsors, self.cfg, self.cv,
        )
        self.assertTrue(accepted, reasons)

    def test_preflight_reports_environment_shape(self):
        output = io.StringIO()
        with mock.patch.object(jobflow, "telegram_api_check", return_value=(False, None)), redirect_stdout(output):
            jobflow.environment_preflight()
        payload = json.loads(output.getvalue())
        self.assertIn("manual_contact_fallback", payload)
        self.assertIn("writable_tmp", payload)
        self.assertIn("libreoffice_export", payload)
        self.assertNotIn("word_export", payload)
        self.assertNotIn("pandoc", payload)

    def test_preflight_validates_telegram_when_configured(self):
        checks = []

        def fake_check(token, method, data=None):
            checks.append((token, method, data))
            return method == "getMe", None

        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "123"}, clear=False), \
             mock.patch.object(jobflow, "telegram_api_check", side_effect=fake_check):
            payload = jobflow.preflight_status()
        self.assertTrue(payload["telegram_token_valid"])
        self.assertFalse(payload["telegram_chat_valid"])
        self.assertIn(("token", "getMe", None), checks)
        self.assertIn(("token", "getChat", {"chat_id": "123"}), checks)

    def test_telegram_api_check_distinguishes_rejection_from_network_failure(self):
        accepted = mock.Mock(ok=True)
        accepted.json.return_value = {"ok": True}
        rejected = mock.Mock(ok=False)
        rejected.json.return_value = {"ok": False}
        with mock.patch.object(jobflow.requests, "post", side_effect=[accepted, rejected]):
            self.assertEqual(jobflow.telegram_api_check("token", "getMe"), (True, None))
            self.assertEqual(jobflow.telegram_api_check("token", "getMe"), (False, None))

        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "123"}, clear=False), \
             mock.patch.object(jobflow.requests, "post", side_effect=requests.ConnectionError("blocked")):
            payload = jobflow.preflight_status()
        self.assertIsNone(payload["telegram_token_valid"])
        self.assertIsNone(payload["telegram_chat_valid"])
        self.assertEqual(payload["telegram_error"], "network")

    def test_next_actions_reports_operational_queues(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            rows = (
                ("screen", "Example", "Data Scientist", "screening", "https://example.test/s", 70),
                ("accept", "Example", "ML Engineer", "accepted", "https://example.test/a", 80),
                ("review", "Example", "AI Engineer", "needs_review", "https://example.test/r", 90),
            )
            for jid, company, title, status, url, relevance in rows:
                conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (jid, company, title, "NL", url, "Python", status, relevance, "[]", "now"))
            conn.execute("INSERT INTO feedback(update_id,job_id,document,text,status,created_at) "
                         "VALUES (1,'review','cv','fix','queued','now')")
            conn.execute("INSERT INTO companies(normalized_name,display_name,career_url,tier,sponsor,consecutive_failures,last_error) "
                         "VALUES ('example','Example','https://example.test/jobs',1,1,2,'blocked')")
            conn.commit(); conn.close()
            artifact = jobflow.job_artifact_folder("review", "Example", "AI Engineer")
            artifact.mkdir(parents=True)
            (artifact / "quality.json").write_text(json.dumps(
                {"status": "NEEDS REVIEW", "blocker": "agent limit", "document_scores": {"cv": 88}}))

            payload = jobflow.next_actions()
            self.assertEqual(payload["counts"]["screening_needs_match"], 1)
            self.assertEqual(payload["counts"]["accepted_needs_documents"], 1)
            self.assertEqual(payload["needs_review"][0]["blocker"], "agent limit")
            self.assertEqual(payload["feedback_queue"][0]["update_id"], 1)
            self.assertEqual(payload["source_attention"][0]["last_error"], "blocked")
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_doctor_report_combines_preflight_and_queues(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            jobflow.db().close()
            with mock.patch.object(jobflow, "preflight_status", return_value={"pdfinfo": True}), \
                 mock.patch.object(jobflow.shutil, "which", return_value="/bin/codex"):
                payload = jobflow.doctor_report()
            self.assertEqual(payload["preflight"], {"pdfinfo": True})
            self.assertTrue(payload["codex_cli"])
            self.assertIn("screening_needs_match", payload["queues"])
            self.assertTrue(payload["master_cv"]["exists"])
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_libreoffice_executable_uses_path_then_platform_locations(self):
        with mock.patch.object(jobflow.shutil, "which", side_effect=lambda name: "/bin/soffice" if name == "soffice" else None):
            self.assertEqual(jobflow.libreoffice_executable(), "/bin/soffice")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "LibreOffice" / "program" / "soffice.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            with mock.patch.object(jobflow.shutil, "which", return_value=None), \
                 mock.patch.dict(os.environ, {"ProgramFiles": str(root)}, clear=False):
                self.assertEqual(jobflow.libreoffice_executable(), str(executable))

    def test_libreoffice_conversion_requires_executable(self):
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(jobflow, "libreoffice_executable", return_value=None):
            root = Path(folder)
            with self.assertRaisesRegex(RuntimeError, "LibreOffice is required"):
                jobflow.convert_docx_with_libreoffice(root / "cv.docx", root / "cv.pdf")

    def test_libreoffice_conversion_reports_process_and_output_failures(self):
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(jobflow, "libreoffice_executable", return_value="soffice"):
            root = Path(folder)
            failed = jobflow.subprocess.CalledProcessError(1, ["soffice"], stderr="conversion failed")
            with mock.patch.object(jobflow.subprocess, "run", side_effect=failed), \
                 self.assertRaisesRegex(RuntimeError, "LibreOffice PDF export failed: conversion failed"):
                jobflow.convert_docx_with_libreoffice(root / "cv.docx", root / "cv.pdf")
            with mock.patch.object(jobflow.subprocess, "run"), \
                 self.assertRaisesRegex(RuntimeError, "LibreOffice did not create"):
                jobflow.convert_docx_with_libreoffice(root / "cv.docx", root / "cv.pdf")

    def test_slm_shadow_is_recorded_without_affecting_job(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA = Path(folder)
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                         "VALUES ('shadow','Example','Data Scientist','NL','https://example.test/shadow',"
                         "'Python role. No visa sponsorship.','screening',80,'[]','2026-01-01')")
            conn.commit(); conn.close()
            payload = {"responsibilities": ["Build models"], "required_skills": ["Python"],
                       "preferred_skills": [], "application_questions": [],
                       "eligibility_flags": [{"category": "visa", "quote": "No visa sponsorship"}]}
            response = mock.Mock()
            response.json.return_value = {"message": {"content": json.dumps(payload)}}
            with mock.patch.object(jobflow.requests, "post", return_value=response):
                jobflow.shadow_extract("shadow")
            check = jobflow.db()
            row = check.execute("SELECT status,result FROM slm_shadow").fetchone()
            self.assertEqual(row["status"], "shadow")
            self.assertTrue(json.loads(row["result"])["eligibility_flags"][0]["quote_valid"])
            self.assertEqual(check.execute("SELECT status FROM jobs").fetchone()[0], "screening")
            check.close()
        jobflow.DATA, jobflow.DB_PATH = old

    def test_match_rejects_below_threshold_and_bad_components(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            for jid in ("low", "bad"):
                conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (jid, "Example", "Data Scientist", "Netherlands", f"https://example.test/{jid}",
                              "Python role", "screening", 60, "[]", "2026-01-01"))
            conn.commit(); conn.close()
            components = {"required_skills": 14, "responsibilities": 10, "seniority_experience": 8,
                          "education_domain": 5, "ats_overlap": 8, "practical_constraints": 4}
            path = root / "low.json"; path.write_text(json.dumps({
                "score": 49, "components": components, **experience_fields()}))
            with mock.patch.object(jobflow, "master_cv", return_value=TEST_EXPERIENCE_CV):
                jobflow.record_match("low", path)
            check = jobflow.db()
            self.assertEqual(check.execute("SELECT status FROM jobs WHERE id='low'").fetchone()[0], "rejected")
            check.close()
            path.write_text(json.dumps({"score": 50, "components": components}))
            with self.assertRaises(SystemExit):
                jobflow.record_match("bad", path)
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_delivery_requires_passing_quality_or_near_pass_score(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                         "VALUES ('job','Example','Data Scientist','NL','https://example.test','Python','accepted',80,'[]','now')")
            conn.commit(); conn.close()
            artifact = jobflow.ARTIFACTS / "job"
            artifact.mkdir(parents=True)
            for name in ("cv.md", "letter.md", "outreach.md"):
                (artifact / name).write_text("# Draft\n")
            (artifact / "quality.json").write_text(json.dumps({"status": "NEEDS REVIEW"}))
            with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "123", "TELEGRAM_BOT_TOKEN": "token"}, clear=False):
                with self.assertRaisesRegex(SystemExit, "no final document score may be below 85"):
                    jobflow.deliver("job")
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_pass_delivery_requires_clean_layout_risks_and_latest_comparisons(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                         "VALUES ('job','Example','Data Scientist','NL','https://example.test','Python','accepted',80,'[]','now')")
            for document in ("cv", "letter"):
                conn.execute("INSERT INTO evaluations VALUES (?,?,?,?,?,?)",
                             ("job", document, 1, 90, json.dumps({"visual_comparison": str(root / f"{document}.png")}), "now"))
            conn.commit(); conn.close()
            artifact = jobflow.ARTIFACTS / "job"; artifact.mkdir(parents=True)
            for name in ("cv.md", "letter.md", "outreach.md"):
                (artifact / name).write_text("# Draft\n")
            quality = artifact / "quality.json"
            quality.write_text(json.dumps({"status": "PASS"}))
            environment = {"TELEGRAM_CHAT_ID": "123", "TELEGRAM_BOT_TOKEN": "token"}
            with mock.patch.dict(os.environ, environment, clear=False):
                with self.assertRaisesRegex(SystemExit, "empty layout_risks"):
                    jobflow.deliver("job")
            quality.write_text(json.dumps({"status": "PASS", "layout_risks": []}))
            with mock.patch.dict(os.environ, environment, clear=False):
                with self.assertRaisesRegex(SystemExit, "latest visual comparisons: cv, letter"):
                    jobflow.deliver("job")
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_telegram_summary_text_pass_has_no_disclaimer(self):
        posted_at = (jobflow.datetime.now(jobflow.timezone.utc).date() - jobflow.timedelta(days=2)).isoformat()
        text = jobflow.telegram_summary_text(
            title="Data Scientist", company="Example", location="NL", url="https://example.test",
            match_score=91, quality_status="PASS", document_scores={"cv": 92, "letter": 90},
            job_summary="Build models for operational decisions.", gaps=["Dutch is A2"],
            caution_scores={}, posted_at=posted_at)
        self.assertIn("Data Scientist — Example", text)
        self.assertIn("📍 NL", text)
        self.assertIn("Brutal match: 91 / 100 | PASS", text)
        self.assertIn("CV ATS: 92 | Letter ATS: 90", text)
        self.assertIn("Main gaps: Dutch is A2", text)
        self.assertIn(f"Posted: {posted_at} (2 days ago)", text)
        self.assertIn("Drafts only; nothing sent to recruiter.", text)
        self.assertNotIn("Care needed", text)

    def test_display_location_simplifies_json_ld(self):
        self.assertEqual(jobflow.display_location("Amsterdam, Netherlands"), "Amsterdam, Netherlands")
        self.assertEqual(jobflow.display_location("{not json"), "{not json")
        location = json.dumps({"@type": "Place", "address": {
            "@type": "PostalAddress", "addressLocality": "Amsterdam", "addressCountry": "NL"}})
        self.assertEqual(jobflow.display_location(location), "Amsterdam, Netherlands")
        location_list = json.dumps([{"@type": "Place", "address": {
            "@type": "PostalAddress", "addressLocality": "Utrecht", "addressCountry": "Netherlands"}}])
        self.assertEqual(jobflow.display_location(location_list), "Utrecht, Netherlands")

    def test_telegram_summary_text_hides_json_location(self):
        location = json.dumps({"@type": "Place", "address": {
            "@type": "PostalAddress", "addressLocality": "Amsterdam", "addressCountry": "NL"}})
        text = jobflow.telegram_summary_text(
            title="Data Scientist", company="Example", location=location, url="https://example.test",
            match_score=91, quality_status="PASS", document_scores={"cv": 92, "letter": 90},
            job_summary="Build models for operational decisions.", gaps=["Dutch is A2"],
            caution_scores={})
        self.assertIn("💼 Data Scientist — Example", text)
        self.assertIn("📍 Amsterdam, Netherlands", text)
        self.assertIn("📊 Brutal match: 91 / 100 | PASS", text)
        self.assertIn("📄 CV ATS: 92 | Letter ATS: 90", text)
        self.assertIn("📝 Build models for operational decisions.", text)
        self.assertIn("⚠️ Main gaps: Dutch is A2", text)
        self.assertIn("🔗 https://example.test", text)
        self.assertIn("📎 Drafts only; nothing sent to recruiter.", text)
        self.assertNotIn("{", text)
        self.assertNotIn("addressLocality", text)
        self.assertNotIn("@type", text)

    def test_telegram_summary_text_one_near_pass_disclaimer(self):
        text = jobflow.telegram_summary_text(
            title="Data Scientist", company="Example", location="NL", url="https://example.test",
            match_score=75, quality_status="NEEDS REVIEW", document_scores={"cv": 88, "letter": 92},
            job_summary="Build models.", gaps=["No direct finance experience"],
            caution_scores={"cv": 88})
        self.assertIn("Care needed", text)
        self.assertIn("cv 88", text)
        self.assertIn("Drafts only; nothing sent to recruiter.", text)

    def test_telegram_summary_text_two_near_pass_disclaimer(self):
        text = jobflow.telegram_summary_text(
            title="Data Scientist", company="Example", location="NL", url="https://example.test",
            match_score=75, quality_status="NEEDS REVIEW", document_scores={"cv": 85, "letter": 89},
            job_summary="Build models.", gaps=["No direct finance experience"],
            caution_scores={"letter": 89, "cv": 85})
        self.assertIn("cv 85", text)
        self.assertIn("letter 89", text)
        self.assertIn("Drafts only; nothing sent to recruiter.", text)

    def test_telegram_summary_text_missing_match_details_fallback(self):
        text = jobflow.telegram_summary_text(
            title="Data Scientist", company="Example", location="NL", url="https://example.test",
            match_score="?", quality_status="PASS", document_scores={},
            job_summary="", gaps=[], caution_scores={})
        self.assertIn("No summary available.", text)
        self.assertIn("Main gaps: none identified", text)
        self.assertIn("CV ATS: ? | Letter ATS: ?", text)
        self.assertIn("Drafts only; nothing sent to recruiter.", text)

    def test_delivery_sends_near_pass_documents_with_disclaimer(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                         "VALUES ('job','Example','Data Scientist','NL','https://example.test','Python','needs_review',80,'[]','now')")
            for document, score_value in (("cv", 88), ("letter", 92)):
                conn.execute("INSERT INTO evaluations VALUES (?,?,?,?,?,?)",
                             ("job", document, 1, score_value, "{}", "now"))
            conn.commit(); conn.close()
            artifact = jobflow.ARTIFACTS / "job"
            artifact.mkdir(parents=True)
            for name in ("cv.md", "letter.md", "outreach.md", "cv.pdf", "letter.pdf", "cv.docx", "letter.docx"):
                (artifact / name).write_text("# Draft\n")
            (artifact / "quality.json").write_text(json.dumps({"status": "NEEDS REVIEW"}))

            messages, sent_files, events = [], [], []
            message_ids = iter(range(100, 110))
            def fake_telegram(method, *, data, files=None):
                if method == "sendMessage":
                    messages.append(data["text"])
                    events.append(("message", None))
                if method == "sendDocument":
                    sent_files.append(files["document"][0])
                    events.append(("document", files["document"][0]))
                return {"message_id": next(message_ids)}

            with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "123", "TELEGRAM_BOT_TOKEN": "token"}, clear=False), \
                 mock.patch.object(jobflow, "candidate_name", return_value="Alex Example"), \
                 mock.patch.object(jobflow, "render_pdf", return_value={"one_page": True, "pages": 1}), \
                 mock.patch.object(jobflow, "telegram", side_effect=fake_telegram):
                jobflow.deliver("job")
            self.assertIn("below 90", messages[0])
            self.assertIn("cv 88", messages[0])
            self.assertEqual(
                [event[0] for event in events],
                ["message", "document", "document", "document", "document", "document"],
            )
            self.assertIn("Alex_Example_CV_Example_Data_Scientist.pdf", sent_files)
            self.assertIn("Alex_Example_Motivation_Letter_Example_Data_Scientist.pdf", sent_files)
            self.assertTrue((artifact / "Alex_Example_CV_Example_Data_Scientist.pdf").exists())
            self.assertTrue((artifact / "Alex_Example_Motivation_Letter_Example_Data_Scientist.docx").exists())
            self.assertTrue((artifact / "Alex_Example_Outreach_Example_Data_Scientist.md").exists())
            self.assertNotIn("job", " ".join(sent_files))
            check = jobflow.db()
            self.assertEqual(check.execute("SELECT status FROM jobs WHERE id='job'").fetchone()[0], "delivered")
            self.assertEqual(check.execute("SELECT COUNT(*) FROM telegram_deliveries").fetchone()[0], 5)
            self.assertEqual(
                set(row[0] for row in check.execute("SELECT DISTINCT document FROM telegram_deliveries")),
                {"cv", "letter", "outreach"},
            )
            check.close()
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_delivery_sends_two_near_pass_documents_with_disclaimer(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                         "VALUES ('job','Example','Data Scientist','NL','https://example.test','Python','needs_review',80,'[]','now')")
            for document, score_value in (("cv", 85), ("letter", 89)):
                conn.execute("INSERT INTO evaluations VALUES (?,?,?,?,?,?)",
                             ("job", document, 1, score_value, "{}", "now"))
            conn.commit(); conn.close()
            artifact = jobflow.ARTIFACTS / "job"
            artifact.mkdir(parents=True)
            for name in ("cv.md", "letter.md", "outreach.md", "cv.pdf", "letter.pdf", "cv.docx", "letter.docx"):
                (artifact / name).write_text("# Draft\n")
            (artifact / "quality.json").write_text(json.dumps({"status": "NEEDS REVIEW"}))

            messages = []
            message_ids = iter(range(100, 110))
            def fake_telegram(method, *, data, files=None):
                if method == "sendMessage":
                    messages.append(data["text"])
                return {"message_id": next(message_ids)}

            with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "123", "TELEGRAM_BOT_TOKEN": "token"}, clear=False), \
                 mock.patch.object(jobflow, "render_pdf", return_value={"one_page": True, "pages": 1}), \
                 mock.patch.object(jobflow, "telegram", side_effect=fake_telegram):
                jobflow.deliver("job")
            self.assertIn("below 90", messages[0])
            self.assertIn("cv 85", messages[0])
            self.assertIn("letter 89", messages[0])
            check = jobflow.db()
            self.assertEqual(check.execute("SELECT status FROM jobs WHERE id='job'").fetchone()[0], "delivered")
            self.assertEqual(check.execute("SELECT COUNT(*) FROM telegram_deliveries").fetchone()[0], 5)
            check.close()
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_delivery_blocks_when_any_document_score_is_below_85(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                         "VALUES ('job','Example','Data Scientist','NL','https://example.test','Python','needs_review',80,'[]','now')")
            for document, score_value in (("cv", 90), ("letter", 84)):
                conn.execute("INSERT INTO evaluations VALUES (?,?,?,?,?,?)",
                             ("job", document, 1, score_value, "{}", "now"))
            conn.commit(); conn.close()
            artifact = jobflow.ARTIFACTS / "job"
            artifact.mkdir(parents=True)
            for name in ("cv.md", "letter.md", "outreach.md"):
                (artifact / name).write_text("# Draft\n")
            (artifact / "quality.json").write_text(json.dumps({"status": "NEEDS REVIEW"}))

            with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "123", "TELEGRAM_BOT_TOKEN": "token"}, clear=False):
                with self.assertRaisesRegex(SystemExit, "no final document score may be below 85"):
                    jobflow.deliver("job")
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_feedback_without_codex_is_queued(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO feedback(update_id,job_id,document,text,created_at) VALUES (1,'job','cv','shorten it','now')")
            conn.commit(); conn.close()
            lookup = jobflow.db()
            item = dict(lookup.execute("SELECT * FROM feedback").fetchone())
            lookup.close()
            with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "123", "CODEX_BIN": ""}, clear=False), \
                 mock.patch.object(jobflow, "telegram", return_value={}), \
                 mock.patch.object(jobflow.shutil, "which", return_value=None):
                jobflow.process_feedback(item)
            check = jobflow.db()
            row = check.execute("SELECT status,last_error FROM feedback WHERE update_id=1").fetchone()
            self.assertEqual(row["status"], "queued")
            self.assertIn("Codex CLI not found", row["last_error"])
            check.close()
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_duplicate_refresh_and_two_miss_closure(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            item = self.job()
            self.assertTrue(jobflow.save_job(conn, item, jobflow.ScreeningResult(True, [], [], [], 80), "example"))
            self.assertFalse(jobflow.save_job(conn, item, jobflow.ScreeningResult(True, [], [], [], 82), "example"))
            marketplace_item = self.job(url="https://linkedin.com/jobs/view/2")
            self.assertTrue(jobflow.save_job(conn, marketplace_item,
                                             jobflow.ScreeningResult(True, [], [], [], 80), "example", "linkedin"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 2)
            self.assertEqual(jobflow.mark_source_misses(conn, "example", set()), 0)
            self.assertEqual(jobflow.mark_source_misses(conn, "example", set()), 1)
            states = {row["discovery_source"]: row["unavailable_at"] for row in conn.execute(
                "SELECT discovery_source,unavailable_at FROM jobs")}
            self.assertIsNotNone(states["direct"])
            self.assertIsNone(states["linkedin"])
            jobflow.save_job(conn, item, jobflow.ScreeningResult(True, [], [], [], 82), "example")
            state = conn.execute("SELECT missing_scans,unavailable_at FROM jobs WHERE discovery_source='direct'").fetchone()
            self.assertEqual(state["missing_scans"], 0)
            self.assertIsNone(state["unavailable_at"])
            conn.close()
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_source_health_backoff_and_success_reset(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA = Path(folder)
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO companies(normalized_name,display_name,career_url,tier,sponsor) "
                         "VALUES ('example','Example','https://example.test/careers',1,1)")
            conn.commit()
            self.assertEqual(jobflow.mark_source_failure(conn, "example", requests.Timeout("slow")), "timeout")
            failed = conn.execute("SELECT consecutive_failures,last_error,next_retry_at FROM companies").fetchone()
            self.assertEqual(failed["consecutive_failures"], 1)
            self.assertIn("timeout", failed["last_error"])
            self.assertIsNotNone(failed["next_retry_at"])
            jobflow.mark_source_success(conn, "example", 4)
            jobflow.mark_source_success(conn, "example", 0)
            healthy = conn.execute("SELECT consecutive_failures,last_error,next_retry_at,last_jobs_found,empty_streak FROM companies").fetchone()
            self.assertEqual(tuple(healthy), (0, None, None, 0, 1))
            jobflow.mark_source_success(conn, "example", 4)
            self.assertEqual(conn.execute("SELECT empty_streak FROM companies").fetchone()[0], 0)
            conn.close()
        jobflow.DATA, jobflow.DB_PATH = old

    def test_cleanup_removes_only_rejected_foreign_jobs(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA, jobflow.DB_PATH = Path(folder), Path(folder) / "jobs.sqlite3"
            conn = jobflow.db()
            for jid, status, location in (("foreign", "rejected", "Paris"),
                                          ("dutch", "rejected", "Amsterdam"),
                                          ("kept", "accepted", "Paris")):
                conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?)", (jid, "Example", "Data", location,
                             f"https://x/{jid}", "", status, 0, "[]", "now"))
            conn.commit()
            self.assertEqual(jobflow.cleanup_out_of_scope_jobs(conn), 1)
            self.assertEqual([row[0] for row in conn.execute("SELECT id FROM jobs ORDER BY id")], ["dutch", "kept"])
            conn.close()
        jobflow.DATA, jobflow.DB_PATH = old

    def test_job_list_combines_availability_and_workflow_status(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA, jobflow.DB_PATH = Path(folder), Path(folder) / "jobs.sqlite3"
            conn = jobflow.db()
            for jid, status in (("screen", "screening"), ("reject", "rejected")):
                conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?)", (jid, "Example", "Data", "Amsterdam",
                             f"https://x/{jid}", "", status, 0, "[]", "now"))
            conn.commit(); conn.close()
            output = io.StringIO()
            with redirect_stdout(output):
                jobflow.list_jobs("active", "screening")
            self.assertEqual([row["id"] for row in json.loads(output.getvalue())], ["screen"])
        jobflow.DATA, jobflow.DB_PATH = old

    def test_job_list_scan_run_isolates_current_jobs_but_unscoped_keeps_backlog(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA, jobflow.DB_PATH = Path(folder), Path(folder) / "jobs.sqlite3"
            conn = jobflow.db()
            for jid, status in (("current-screen", "screening"), ("stale-screen", "screening"),
                                ("current-accepted", "accepted"), ("backlog-accepted", "accepted")):
                conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,discovered_at) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?)", (jid, "Example", "Data", "Amsterdam",
                             f"https://x/{jid}", "", status, 50, "[]", "now"))
            conn.execute("INSERT INTO scan_runs(started_at,finished_at,screening_job_ids) VALUES (?,?,?)",
                         ("now", "now", json.dumps(["current-screen", "current-accepted"])))
            conn.commit(); conn.close()

            def listed(status, scan_run=None):
                output = io.StringIO()
                with redirect_stdout(output):
                    jobflow.list_jobs("active", status, scan_run)
                return [row["id"] for row in json.loads(output.getvalue())]

            self.assertEqual(listed("screening", "latest"), ["current-screen"])
            self.assertEqual(listed("accepted", "latest"), ["current-accepted"])
            self.assertEqual(set(listed("accepted")), {"current-accepted", "backlog-accepted"})

            conn = jobflow.db()
            conn.execute("INSERT INTO scan_runs(started_at,finished_at) VALUES ('later','later')")
            conn.commit(); conn.close()
            self.assertEqual(listed("screening", "latest"), [])
        jobflow.DATA, jobflow.DB_PATH = old

    def test_job_list_orders_by_original_match_then_posting_date(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA, jobflow.DB_PATH = Path(folder), Path(folder) / "jobs.sqlite3"
            conn = jobflow.db()
            for jid, score, posted in (("high", 90, "2026-06-01"), ("recent", 80, "2026-07-08"),
                                       ("older", 80, "2026-07-01")):
                conn.execute("INSERT INTO jobs(id,company,title,url,description,status,relevance,reasons,discovered_at,posted_at) "
                             "VALUES (?,?,?,?,?,'accepted',80,'[]','now',?)",
                             (jid, "Example", "Data", f"https://x/{jid}", "", posted))
                conn.execute("INSERT INTO job_matches VALUES (?,?,?,?)", (jid, score, "{}", "now"))
            conn.commit(); conn.close()
            output = io.StringIO()
            with redirect_stdout(output):
                jobflow.list_jobs("active", "accepted")
            self.assertEqual([row["id"] for row in json.loads(output.getvalue())], ["high", "recent", "older"])
        jobflow.DATA, jobflow.DB_PATH = old

    def test_prune_keeps_tombstone_and_accepted_artifacts(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            old_date = "2020-01-01T00:00:00+00:00"
            for jid, status in (("rejected", "rejected"), ("accepted", "accepted")):
                conn.execute("INSERT INTO jobs(id,company,title,location,url,description,status,relevance,reasons,"
                             "discovered_at,unavailable_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (jid, "Example", "Data", "NL", f"https://example.test/{jid}", "large text",
                              status, 50, "[]", old_date, old_date))
                (jobflow.ARTIFACTS / jid).mkdir(parents=True)
                (jobflow.ARTIFACTS / jid / "file.md").write_text("x")
            conn.commit(); conn.close()
            jobflow.prune()
            check = jobflow.db()
            rejected = check.execute("SELECT status,description,url FROM jobs WHERE id='rejected'").fetchone()
            self.assertEqual((rejected["status"], rejected["description"]), ("archived", ""))
            self.assertFalse((jobflow.ARTIFACTS / "rejected").exists())
            self.assertTrue((jobflow.ARTIFACTS / "accepted" / "file.md").exists())
            check.close()
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_record_outcome_archives_files_snapshots_scores_and_is_idempotent(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,url,description,status,relevance,reasons,discovered_at) "
                         "VALUES ('job','Example','Data Scientist','https://x/job','','delivered',80,'[]','now')")
            conn.execute("INSERT INTO job_matches VALUES ('job',78,'{}','now')")
            conn.execute("INSERT INTO evaluations VALUES ('job','cv',1,91,'{}','now')")
            conn.execute("INSERT INTO evaluations VALUES ('job','letter',1,92,'{}','now')")
            conn.commit(); conn.close()
            submitted = root / "submitted.pdf"
            submitted.write_bytes(b"submitted")
            event = root / "outcome.json"
            event.write_text(json.dumps({"status": "applied", "occurred_at": "2026-07-08",
                                         "channel": "company site", "submitted_files": [str(submitted)]}))
            first, duplicate = io.StringIO(), io.StringIO()
            with redirect_stdout(first):
                jobflow.record_outcome("job", event)
            with redirect_stdout(duplicate):
                jobflow.record_outcome("job", event)
            conn = jobflow.db()
            application = conn.execute("SELECT * FROM applications WHERE job_id='job'").fetchone()
            self.assertEqual((application["match_score"], application["cv_score"], application["letter_score"]),
                             (78, 91, 92))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM application_events").fetchone()[0], 1)
            self.assertTrue(json.loads(duplicate.getvalue())["duplicate"])
            archived = jobflow.ARTIFACTS / "job" / "submitted" / submitted.name
            self.assertEqual(archived.read_bytes(), b"submitted")
            conn.execute("INSERT INTO evaluations VALUES ('job','cv',2,99,'{}','later')")
            conn.commit(); conn.close()
            interview = root / "interview.json"
            interview.write_text(json.dumps({"status": "interview", "stage": "phone",
                                              "occurred_at": "2026-07-10T10:00:00+02:00"}))
            with redirect_stdout(io.StringIO()):
                jobflow.record_outcome("job", interview)
            conn = jobflow.db()
            application = conn.execute("SELECT * FROM applications WHERE job_id='job'").fetchone()
            self.assertEqual((application["status"], application["stage"], application["cv_score"]),
                             ("interview", "phone", 91))
            conn.close()
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_record_outcome_rejects_missing_file_and_backward_transition(self):
        old = jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jobflow.DATA, jobflow.ARTIFACTS = root / "data", root / "artifacts"
            jobflow.DB_PATH = jobflow.DATA / "jobs.sqlite3"
            conn = jobflow.db()
            conn.execute("INSERT INTO jobs(id,company,title,url,description,status,relevance,reasons,discovered_at) "
                         "VALUES ('job','Example','Data','https://x/job','','delivered',80,'[]','now')")
            conn.commit(); conn.close()
            event = root / "outcome.json"
            event.write_text(json.dumps({"status": "applied", "occurred_at": "2026-07-08",
                                         "submitted_files": [str(root / "missing.pdf")]}))
            with self.assertRaisesRegex(SystemExit, "submitted file not found"):
                jobflow.record_outcome("job", event)
            event.write_text(json.dumps({"status": "hired", "occurred_at": "2026-07-20"}))
            with redirect_stdout(io.StringIO()):
                jobflow.record_outcome("job", event)
            event.write_text(json.dumps({"status": "interview", "occurred_at": "2026-07-21"}))
            with self.assertRaisesRegex(SystemExit, "invalid outcome transition"):
                jobflow.record_outcome("job", event)
        jobflow.DATA, jobflow.ARTIFACTS, jobflow.DB_PATH = old

    def test_outcome_schema_enums_match_runtime_contract(self):
        schema = json.loads((Path(__file__).parents[1] / "application_outcome.schema.json").read_text())
        self.assertEqual(set(schema["properties"]["status"]["enum"]), jobflow.OUTCOME_STATUSES)
        self.assertEqual(set(schema["properties"]["stage"]["enum"]), jobflow.OUTCOME_STAGES)

    def test_public_release_contains_no_private_markers_or_tracked_profile(self):
        root = Path(__file__).parents[1]
        if (root / ".git").exists():
            tracked = subprocess.check_output(["git", "ls-files"], cwd=root, text=True).splitlines()
        else:
            tracked = [str(path.relative_to(root)) for path in root.rglob("*") if path.is_file() and
                       path.name not in {"master_cv.md", "config.yaml", ".env"} and
                       not any(part in {".git", ".venv", "data", "artifacts", "__pycache__"} for part in path.parts)]
        self.assertNotIn("master_cv.md", tracked)
        self.assertNotIn("config.yaml", tracked)
        self.assertIn("master_cv.example.md", tracked)
        self.assertIn("config.example.yaml", tracked)
        text = "\n".join((root / name).read_text(errors="ignore") for name in tracked
                          if (root / name).suffix in {".py", ".md", ".toml", ".yaml", ".yml", ".json"})
        for marker in ("Marco " + "Mo", "/home/" + "marco", "mico" + "bruh",
                       "ADC " + "Nederland", "Sim" + "Energy"):
            self.assertNotIn(marker, text)

    def test_outcome_report_uses_minimum_samples_and_never_changes_config(self):
        old = jobflow.DATA, jobflow.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            jobflow.DATA, jobflow.DB_PATH = Path(folder), Path(folder) / "jobs.sqlite3"
            conn = jobflow.db()
            for index in range(10):
                jid = f"job{index}"
                score = 95 if index < 5 else 65
                conn.execute("INSERT INTO jobs(id,company,title,url,description,status,relevance,reasons,discovered_at,"
                             "discovery_source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (jid, "Example", "Data", f"https://x/{jid}", "", "delivered", 80, "[]", "now", "direct"))
                conn.execute("INSERT INTO applications VALUES (?,?,?,?,?,?,?,?,?)",
                             (jid, "rejected", None, "2026-01-01", "2026-02-01", "site", score, 90, 90))
                conn.execute("INSERT INTO application_events VALUES (?,?,?,?,?,?,?,?,?)",
                             (f"applied{index}", jid, "applied", None, "2026-01-01", None, None, "{}", "now"))
                if index < 5:
                    conn.execute("INSERT INTO application_events VALUES (?,?,?,?,?,?,?,?,?)",
                                 (f"interview{index}", jid, "interview", "phone", "2026-01-02",
                                  None, None, "{}", "now"))
            conn.commit(); conn.close()
            output = io.StringIO()
            with redirect_stdout(output):
                jobflow.outcome_report()
            report = json.loads(output.getvalue())
            self.assertTrue(report["advisory_only"])
            self.assertEqual(report["resolved"], 10)
            self.assertEqual(report["by_match_score"]["90-100"]["interview_rate"], 100.0)
            self.assertEqual(report["by_match_score"]["60-69"]["interview_rate"], 0.0)
            self.assertEqual(len(report["observations"]), 1)
        jobflow.DATA, jobflow.DB_PATH = old


if __name__ == "__main__":
    unittest.main()

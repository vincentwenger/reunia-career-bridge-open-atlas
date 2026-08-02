from __future__ import annotations

import json
import unittest

from job_discovery.models import JobSourceType
from job_discovery.source_import import (
    CompanySourceImportError,
    parse_company_source_import,
)


class CompanySourceImportTests(unittest.TestCase):
    def test_parses_csv_with_user_facing_headers(self) -> None:
        content = (
            "Company,Source type,ATS site identifier,Career-page URL,Enabled\n"
            "Intel,Workday,,https://intel.wd1.myworkdayjobs.com/External,true\n"
            "Banner Bank,Lever,bannerbank,https://jobs.lever.co/bannerbank,yes\n"
        ).encode("utf-8")

        rows = parse_company_source_import("companies.csv", content)

        self.assertEqual(2, len(rows))
        self.assertEqual("Intel", rows[0].company_name)
        self.assertEqual(JobSourceType.WORKDAY, rows[0].source_type)
        self.assertEqual("", rows[0].source_identifier)
        self.assertTrue(rows[0].enabled)
        self.assertEqual("bannerbank", rows[1].source_identifier)

    def test_parses_json_wrapper_and_manual_alias(self) -> None:
        content = json.dumps(
            {
                "companies": [
                    {
                        "company": "Example Employer",
                        "source_type": "Manual career-page URL (JSON-LD)",
                        "career_page_url": "https://example.com/careers",
                        "enabled": False,
                    }
                ]
            }
        ).encode("utf-8")

        rows = parse_company_source_import("companies.json", content)

        self.assertEqual(1, len(rows))
        self.assertEqual(JobSourceType.GENERIC_JSONLD, rows[0].source_type)
        self.assertFalse(rows[0].enabled)

    def test_accepts_sap_successfactors_aliases(self) -> None:
        content = (
            "Company,Source type,ATS site identifier,Career-page URL,Enabled\n"
            "SAP,SAP SuccessFactors,,https://jobs.sap.com/,true\n"
        ).encode("utf-8")

        rows = parse_company_source_import("companies.csv", content)

        self.assertEqual(JobSourceType.SUCCESSFACTORS, rows[0].source_type)
        self.assertEqual("https://jobs.sap.com/", rows[0].careers_url)

    def test_accepts_oracle_cloud_hcm_aliases(self) -> None:
        csv_content = (
            "Company,Source type,ATS site identifier,Career-page URL,Enabled\n"
            "Oracle,Oracle Cloud HCM,,https://careers.oracle.com/en/sites/jobsearch/jobs,true\n"
        ).encode("utf-8")
        json_content = json.dumps(
            [
                {
                    "company": "Example",
                    "source_type": "Oracle Recruiting Cloud",
                    "careers_url": (
                        "https://example.fa.us2.oraclecloud.com/"
                        "hcmUI/CandidateExperience/en/sites/CX_1/jobs"
                    ),
                }
            ]
        ).encode("utf-8")

        csv_rows = parse_company_source_import("companies.csv", csv_content)
        json_rows = parse_company_source_import("companies.json", json_content)

        self.assertEqual(JobSourceType.ORACLE_CLOUD_HCM, csv_rows[0].source_type)
        self.assertEqual(JobSourceType.ORACLE_CLOUD_HCM, json_rows[0].source_type)
        self.assertEqual("", csv_rows[0].source_identifier)

    def test_accepts_icims_aliases(self) -> None:
        content = (
            "Company,Source type,ATS site identifier,Career-page URL,Enabled\n"
            "Example,iCIMS,,https://careers-example.icims.com/jobs/search,true\n"
        ).encode("utf-8")

        rows = parse_company_source_import("companies.csv", content)

        self.assertEqual(JobSourceType.ICIMS, rows[0].source_type)
        self.assertEqual("", rows[0].source_identifier)

    def test_accepts_additional_public_ats_aliases(self) -> None:
        content = (
            "Company,Source type,ATS site identifier,Career-page URL,Enabled\n"
            "One,SmartRecruiters,,https://careers.smartrecruiters.com/One,true\n"
            "Two,Avature,,https://two.avature.net/en_US/careers/SearchJobs,true\n"
            "Three,Eightfold AI,,https://app.eightfold.ai/careers?domain=three.example,true\n"
            "Four,Oracle Taleo,,https://four.taleo.net/careersection/external/jobsearch.ftl,true\n"
            "Five,Dayforce HCM,,https://jobs.dayforcehcm.com/en-US/five/CAREERS,true\n"
            "Six,Talemetry / TTC Portals,,https://six.ttcportals.com/search/jobs,true\n"
            "Six B,Jobvite,,https://jobs.jobvite.com/sixb/search,true\n"
            "Seven,UKG Pro / UltiPro,,https://recruiting2.ultipro.com/SEV1000/JobBoard/12345678-1234-1234-1234-123456789abc,true\n"
            "Eight,PeopleAdmin,,https://eight.peopleadmin.com/postings/search,true\n"
            "Nine,Radancy / TalentBrew,,https://jobs.nine.example/search-jobs,true\n"
            "Ten,Amazon Jobs,,https://www.amazon.jobs/en/search?country=USA,true\n"
            "Eleven,Branded Requisition Portal,,https://careers.eleven.example/api/requisitions/search,true\n"
        ).encode("utf-8")

        rows = parse_company_source_import("companies.csv", content)

        self.assertEqual(
            [
                JobSourceType.SMARTRECRUITERS,
                JobSourceType.AVATURE,
                JobSourceType.EIGHTFOLD,
                JobSourceType.TALEO,
                JobSourceType.DAYFORCE,
                JobSourceType.TALEMETRY_TTC,
                JobSourceType.JOBVITE,
                JobSourceType.UKG_PRO,
                JobSourceType.PEOPLEADMIN,
                JobSourceType.RADANCY_TALENTBREW,
                JobSourceType.AMAZON_JOBS,
                JobSourceType.BRANDED_REQUISITION,
            ],
            [row.source_type for row in rows],
        )
        self.assertTrue(all(row.source_identifier == "" for row in rows))

    def test_accepts_json_array_and_normalized_field_names(self) -> None:
        content = json.dumps(
            [
                {
                    "company_name": "Nike",
                    "type": "workday",
                    "careers_url": "https://nike.wd1.myworkdayjobs.com/nke",
                }
            ]
        ).encode("utf-8")

        rows = parse_company_source_import("sources.json", content)

        self.assertEqual("Nike", rows[0].company_name)
        self.assertTrue(rows[0].enabled)

    def test_rejects_invalid_rows_as_one_validation_result(self) -> None:
        content = (
            "Company,Source type,ATS site identifier,Career-page URL\n"
            ",Workday,,https://intel.wd1.myworkdayjobs.com/External\n"
            "Example,Unknown ATS,,https://example.com/jobs\n"
        ).encode("utf-8")

        with self.assertRaises(CompanySourceImportError) as context:
            parse_company_source_import("companies.csv", content)

        message = str(context.exception)
        self.assertIn("Row 2: company is required", message)
        self.assertIn("Row 3:", message)

    def test_rejects_unsupported_extension_and_oversized_files(self) -> None:
        with self.assertRaisesRegex(CompanySourceImportError, "Only .csv and .json"):
            parse_company_source_import("companies.xlsx", b"not-used")
        with self.assertRaisesRegex(CompanySourceImportError, "too large"):
            parse_company_source_import("companies.csv", b"a" * 11, max_bytes=10)

    def test_rejects_more_than_configured_row_limit(self) -> None:
        content = (
            "Company,Source type,ATS site identifier,Career-page URL\n"
            "One,Lever,one,\n"
            "Two,Lever,two,\n"
        ).encode("utf-8")

        with self.assertRaisesRegex(CompanySourceImportError, "maximum is 1"):
            parse_company_source_import("companies.csv", content, max_rows=1)


if __name__ == "__main__":
    unittest.main()

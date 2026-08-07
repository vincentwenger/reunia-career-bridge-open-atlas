from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PerformanceMaintainabilityContractTests(unittest.TestCase):
    def test_translation_catalog_is_split_and_loaded_on_demand(self) -> None:
        runtime = (ROOT / "products/reunia/static/js/i18n.js").read_text(encoding="utf-8")
        catalog = (ROOT / "products/reunia/static/js/i18n-fr.js").read_text(encoding="utf-8")
        base = (ROOT / "products/reunia/templates/base.html").read_text(encoding="utf-8")
        self.assertLess(len(runtime), 30_000)
        self.assertIn("window.ReuniaTranslations.fr", catalog)
        self.assertIn("data-i18n-fr-src", base)
        self.assertIn("loadFrenchCatalog", runtime)

    def test_job_discovery_script_is_page_scoped(self) -> None:
        base = (ROOT / "products/resume_taylor/templates/application_builder/base.html").read_text(encoding="utf-8")
        discovery = (ROOT / "products/resume_taylor/templates/application_builder/job_discovery.html").read_text(encoding="utf-8")
        self.assertIn("app-shell.js", base)
        self.assertNotIn("app-job-discovery.js", base)
        self.assertIn("app-job-discovery.js", discovery)

    def test_application_builder_uses_only_page_scoped_javascript(self) -> None:
        static_root = ROOT / "products/resume_taylor/static"
        templates = ROOT / "products/resume_taylor/templates/application_builder"
        base = (templates / "base.html").read_text(encoding="utf-8")
        discovery = (templates / "job_discovery.html").read_text(encoding="utf-8")
        workflow = (templates / "index.html").read_text(encoding="utf-8")
        preparation = (templates / "interview_preparation.html").read_text(encoding="utf-8")

        self.assertFalse((static_root / "app.js").exists())
        self.assertFalse((static_root / "app.min.js").exists())
        self.assertNotIn("app.js", "\n".join(
            path.read_text(encoding="utf-8") for path in templates.rglob("*.html")
        ))
        self.assertIn("app-shell.js", base)
        self.assertIn("app-job-discovery.js", discovery)
        self.assertIn("app-resume-workflow.js", workflow)
        self.assertIn("resume-async-jobs.js", workflow)
        self.assertIn("interview-preparation.js", preparation)

    def test_shared_design_tokens_are_loaded_first(self) -> None:
        tokens = (ROOT / "products/reunia/static/css/design-tokens.css").read_text(encoding="utf-8")
        shared_styles = (ROOT / "products/reunia/templates/components/common_page_styles.html").read_text(encoding="utf-8")
        base = (ROOT / "products/reunia/templates/base.html").read_text(encoding="utf-8")
        builder = (ROOT / "products/resume_taylor/templates/application_builder/base.html").read_text(encoding="utf-8")
        self.assertIn("--cb-space-4", tokens)
        self.assertIn("--cb-shadow-md", tokens)
        self.assertLess(shared_styles.index("design-tokens.css"), shared_styles.index("base.css"))
        self.assertIn("common_page_styles.html", base)
        self.assertIn("common_page_styles.html", builder)

    def test_profile_is_not_duplicated_in_data_attributes(self) -> None:
        template = (ROOT / "products/reunia/templates/knowledge.html").read_text(encoding="utf-8")
        script = (ROOT / "products/reunia/static/js/pages/knowledge.js").read_text(encoding="utf-8")
        self.assertNotIn("data-profile-professional-headline", template)
        self.assertNotIn("data-profile-constraints", template)
        self.assertIn("fetch(endpoints.context", script)

    def test_asset_budget_and_build_guardrails_exist(self) -> None:
        self.assertTrue((ROOT / "config" / "quality" / "asset-budgets.json").is_file())
        build_script = ROOT / "scripts/build_static_assets.py"
        workflow_path = ROOT / ".github/workflows/asset-budget.yml"
        self.assertTrue(build_script.is_file())
        self.assertTrue(workflow_path.is_file())

        build_source = build_script.read_text(encoding="utf-8")
        workflow = workflow_path.read_text(encoding="utf-8")
        self.assertIn('"--check"', build_source)
        self.assertIn("python scripts/build_static_assets.py --check", workflow)
        self.assertIn("python scripts/check_asset_budgets.py", workflow)
        self.assertIn("python scripts/check_common_page_assets.py", workflow)
        self.assertIn(
            "python -m unittest tests.contracts.test_performance_maintainability",
            workflow,
        )

    def test_oversized_business_modules_are_split_into_bounded_components(self) -> None:
        route_root = ROOT / "products/resume_taylor/application_builder_routes"
        service_root = ROOT / "products/reunia/meeting_assistant/services"
        report_root = ROOT / "products/resume_taylor/resume_tailor"
        storage_root = ROOT / "job_discovery"

        facades = {
            route_root / "job_discovery.py": 80,
            route_root / "resume_workflow.py": 80,
            report_root / "resume_report.py": 650,
            service_root / "admin_analytics_service.py": 650,
            storage_root / "storage.py": 100,
            service_root / "mock_interview_service.py": 500,
        }
        for path, maximum_lines in facades.items():
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(
                line_count,
                maximum_lines,
                f"{path.relative_to(ROOT)} grew beyond its composition-facade budget",
            )

        families = {
            route_root / "job_discovery_routes": 1000,
            route_root / "resume_workflow_routes": 1000,
        }
        for directory, maximum_lines in families.items():
            for path in directory.glob("*.py"):
                if path.name == "__init__.py":
                    continue
                self.assertLessEqual(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    maximum_lines,
                    f"{path.relative_to(ROOT)} should remain a cohesive route component",
                )

        prefixed_families = (
            (report_root, "resume_report_", 800),
            (service_root, "admin_analytics_", 700),
            (storage_root, "storage_", 700),
            (service_root, "mock_interview_", 600),
        )
        for directory, prefix, maximum_lines in prefixed_families:
            for path in directory.glob(f"{prefix}*.py"):
                self.assertLessEqual(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    maximum_lines,
                    f"{path.relative_to(ROOT)} should remain a bounded component",
                )

    def test_route_registrars_do_not_define_handlers_inside_register(self) -> None:
        route_root = ROOT / "products/resume_taylor/application_builder_routes"
        for name in ("job_discovery.py", "resume_workflow.py"):
            path = route_root / name
            tree = ast.parse(path.read_text(encoding="utf-8"))
            register = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "register"
            )
            nested_handlers = [
                node
                for node in ast.walk(register)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node is not register
            ]
            self.assertEqual(
                nested_handlers,
                [],
                f"{name} must register top-level, independently testable handlers",
            )


if __name__ == "__main__":
    unittest.main()

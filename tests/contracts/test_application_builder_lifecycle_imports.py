from __future__ import annotations

import unittest

from products.resume_taylor.application_builder_routes import lifecycle
from products.resume_taylor.application_builder_routes.job_discovery_routes import workspace_view


class ApplicationBuilderTimingImportTests(unittest.TestCase):
    def test_perf_counter_is_available_to_request_hooks(self) -> None:
        for module in (lifecycle, workspace_view):
            with self.subTest(module=module.__name__):
                started_at = module.perf_counter()
                self.assertIsInstance(started_at, float)
                self.assertGreater(started_at, 0.0)


if __name__ == "__main__":
    unittest.main()

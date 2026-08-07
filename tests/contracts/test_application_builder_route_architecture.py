from __future__ import annotations

import unittest

from scripts.check_application_builder_route_architecture import validate


class ApplicationBuilderRouteArchitectureContractTests(unittest.TestCase):
    def test_routes_remain_split_by_feature(self) -> None:
        self.assertEqual(validate(), [])


if __name__ == "__main__":
    unittest.main()

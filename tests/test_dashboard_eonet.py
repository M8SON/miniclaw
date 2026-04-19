import unittest
from unittest.mock import patch

from containers.dashboard.eonet import (
    build_priority_hazards,
    fetch_eonet_events,
    normalize_event,
)


OPEN_WILDFIRE = {
    "id": "EONET_1",
    "title": "Large wildfire near population center",
    "closed": None,
    "categories": [{"id": "wildfires", "title": "Wildfires"}],
    "sources": [{"id": "InciWeb", "url": "https://example.invalid/fire"}],
    "geometry": [
        {
            "date": "2026-04-19T10:00:00Z",
            "type": "Point",
            "coordinates": [-72.6, 44.3],
        }
    ],
}

OPEN_DUST = {
    "id": "EONET_2",
    "title": "Regional dust event",
    "closed": None,
    "categories": [{"id": "dustHaze", "title": "Dust and Haze"}],
    "sources": [{"id": "NASA", "url": "https://example.invalid/dust"}],
    "geometry": [
        {
            "date": "2026-04-19T08:00:00Z",
            "type": "Point",
            "coordinates": [15.0, 22.0],
        }
    ],
}

POLYGON_WILDFIRE = {
    "id": "EONET_3",
    "title": "Wildfire affecting a nearby area",
    "closed": None,
    "categories": [{"id": "wildfires", "title": "Wildfires"}],
    "sources": [{"id": "NASA", "url": "https://example.invalid/polygon-fire"}],
    "geometry": [
        {
            "date": "2026-04-19T09:00:00Z",
            "type": "Polygon",
            "coordinates": [
                [
                    [-73.40, 44.35],
                    [-73.10, 44.35],
                    [-73.10, 44.55],
                    [-73.40, 44.55],
                    [-73.40, 44.35],
                ]
            ],
        }
    ],
}

SEVERE_STORM = {
    "id": "EONET_4",
    "title": "Severe storm over open water",
    "closed": None,
    "categories": [{"id": "severeStorms", "title": "Severe Storms"}],
    "sources": [{"id": "NASA", "url": "https://example.invalid/storm"}],
    "geometry": [
        {
            "date": "2026-04-19T11:00:00Z",
            "type": "Point",
            "coordinates": [-10.0, 10.0],
        }
    ],
}


class DashboardEONETTests(unittest.TestCase):
    def test_normalize_event_extracts_dashboard_fields(self):
        item = normalize_event(
            OPEN_WILDFIRE,
            focus_location={"name": "Burlington", "lat": 44.47, "lon": -73.21},
        )

        self.assertEqual(item["event_id"], "EONET_1")
        self.assertEqual(item["title"], "Large wildfire near population center")
        self.assertEqual(item["category"], "wildfires")
        self.assertEqual(item["category_label"], "Wildfires")
        self.assertEqual(item["source_label"], "InciWeb")
        self.assertEqual(item["source_url"], "https://example.invalid/fire")
        self.assertEqual(item["date"], "2026-04-19T10:00:00Z")
        self.assertTrue(item["is_open"])
        self.assertIn("score", item)
        self.assertIn("region_label", item)
        self.assertIn("magnitude_label", item)

    def test_build_priority_hazards_prefers_major_hazard_over_lower_signal_item(self):
        ranked = build_priority_hazards(
            [SEVERE_STORM, OPEN_WILDFIRE],
            hazard_cfg={"enabled": True, "limit": 3, "min_score": 40},
            focus_location=None,
            now_ts=1776596400,
        )

        self.assertEqual(ranked[0]["category"], "wildfires")
        self.assertEqual(ranked[1]["category"], "severeStorms")
        self.assertEqual(len(ranked), 2)

    def test_build_priority_hazards_returns_empty_when_all_items_are_below_threshold(self):
        ranked = build_priority_hazards(
            [OPEN_DUST],
            hazard_cfg={"enabled": True, "limit": 3, "min_score": 70},
            focus_location=None,
            now_ts=1776596400,
        )

        self.assertEqual(ranked, [])

    def test_build_priority_hazards_returns_empty_when_disabled(self):
        ranked = build_priority_hazards(
            [OPEN_WILDFIRE],
            hazard_cfg={"enabled": False, "limit": 3, "min_score": 40},
            focus_location={"name": "Burlington", "lat": 44.47, "lon": -73.21},
            now_ts=1776596400,
        )

        self.assertEqual(ranked, [])

    def test_normalize_event_uses_polygon_geometry_for_local_relevance(self):
        far_item = normalize_event(POLYGON_WILDFIRE)
        near_item = normalize_event(
            POLYGON_WILDFIRE,
            focus_location={"name": "Burlington", "lat": 44.47, "lon": -73.21},
        )

        self.assertNotEqual(near_item["region_label"], "Global")
        self.assertGreater(near_item["score"], far_item["score"])

    @patch("containers.dashboard.eonet.requests.get")
    def test_fetch_eonet_events_returns_empty_list_on_http_failure(self, mock_get):
        mock_get.side_effect = RuntimeError("boom")

        self.assertEqual(fetch_eonet_events({"enabled": True}), [])


if __name__ == "__main__":
    unittest.main()

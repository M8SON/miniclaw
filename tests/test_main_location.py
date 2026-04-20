import os
import unittest
from unittest.mock import patch

import main


class MainLocationTests(unittest.TestCase):
    def test_build_startup_context_uses_remembered_location_instead_of_env(self):
        with patch.dict(
            os.environ,
            {
                "OPENWEATHER_API_KEY": "test-key",
                "WEATHER_LOCATION": "Miami,FL",
            },
            clear=False,
        ), patch("main.resolve_location", return_value="Denver,CO"), patch(
            "main._fetch_weather_for_context",
            return_value="Weather in Denver: 55°F, clear sky.",
        ) as fetch_weather:
            context = main._build_startup_context()

        fetch_weather.assert_called_once_with("Denver,CO", "test-key")
        self.assertIn("Weather in Denver", context)

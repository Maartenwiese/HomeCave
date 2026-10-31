"""Static anti-mocking AST audit, brand/icon verification, and Home Assistant schema checks."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import struct
import unittest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DIR = REPO_ROOT / "custom_components" / "homecave"

FORBIDDEN_TOKENS = (
    "mock_success",
    "fake_fallback",
    "dummy_wine",
    "MOCK_VIVINO",
    "FAKE_GEMINI_RESPONSE",
)


def _read_png_dimensions(path: Path) -> tuple[int, int]:
    """Read width and height from a PNG IHDR chunk using only Python stdlib."""
    raw = path.read_bytes()
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        raise ValueError(f"Invalid PNG header in {path}")
    width, height = struct.unpack(">II", raw[16:24])
    return width, height


class _HABlueprintYamlLoader(yaml.SafeLoader):
    """YAML Loader supporting Home Assistant's !input tag in Blueprints."""


_HABlueprintYamlLoader.add_constructor(
    "!input",
    lambda loader, node: f"!input {loader.construct_scalar(node)}",
)


class TestStaticAntiMockingAndSchemas(unittest.TestCase):
    """Verify zero hardcoded mock fallbacks, brand/icon integrity, and JSON/YAML schemas."""

    def test_no_hardcoded_mock_fallbacks_in_production_code(self) -> None:
        """Audit production Python modules to enforce Zero-Mocking engineering standard."""
        py_files = list(INTEGRATION_DIR.glob("*.py"))
        self.assertGreaterEqual(len(py_files), 8)

        for py_file in py_files:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            self.assertIsNotNone(tree)

            lower_source = source.lower()
            for token in FORBIDDEN_TOKENS:
                self.assertNotIn(
                    token.lower(),
                    lower_source,
                    f"Forbidden mock token '{token}' found in {py_file}",
                )

    def test_manifest_and_translations_valid_json(self) -> None:
        """Verify manifest.json Hassfest key order, strings.json, and translations/en.json."""
        manifest_text = (INTEGRATION_DIR / "manifest.json").read_text(encoding="utf-8")
        manifest = json.loads(manifest_text)
        self.assertEqual(manifest["domain"], "homecave")
        self.assertEqual(manifest["name"], "HomeCave")
        self.assertEqual(manifest["version"], "0.9.1")
        self.assertTrue(manifest["config_flow"])
        self.assertEqual(
            manifest["documentation"],
            "https://github.com/Maartenwiese/HomeCave",
        )
        self.assertEqual(
            manifest["issue_tracker"],
            "https://github.com/Maartenwiese/HomeCave/issues",
        )

        # Hassfest strict key ordering: domain, name, then alphabetical
        keys = list(manifest.keys())
        expected_keys = ["domain", "name"] + sorted(
            k for k in keys if k not in ("domain", "name")
        )
        self.assertEqual(keys, expected_keys)

        strings = json.loads(
            (INTEGRATION_DIR / "strings.json").read_text(encoding="utf-8")
        )
        en_trans = json.loads(
            (INTEGRATION_DIR / "translations" / "en.json").read_text(encoding="utf-8")
        )
        self.assertEqual(strings, en_trans)
        self.assertIn("llm_provider", strings["config"]["step"]["user"]["data"])
        self.assertIn("gemini_model", strings["config"]["step"]["user"]["data"])
        self.assertIn("claude_model", strings["config"]["step"]["user"]["data"])

    def test_dashboard_blueprint_and_services_valid_yaml(self) -> None:
        """Verify dashboard YAML uses sections view, HomeCave cards/entities, and valid blueprint."""
        services_path = INTEGRATION_DIR / "services.yaml"
        services_data = yaml.safe_load(services_path.read_text(encoding="utf-8"))
        self.assertIn("scan_bottle", services_data)
        self.assertIn("add_wine", services_data)
        self.assertIn("adjust_quantity", services_data)
        self.assertIn("update_wine", services_data)

        dashboard_path = REPO_ROOT / "dashboards" / "homecave-dashboard.yaml"
        dashboard_data = yaml.safe_load(dashboard_path.read_text(encoding="utf-8"))
        self.assertEqual(dashboard_data["title"], "HomeCave")
        views = dashboard_data["views"]
        self.assertEqual(views[0]["type"], "sections")
        sections = views[0]["sections"]
        self.assertGreaterEqual(len(sections), 2)

        card_types = [
            card["type"]
            for section in sections
            for card in section.get("cards", [])
        ]
        self.assertIn("heading", card_types)
        self.assertIn("tile", card_types)
        self.assertIn("distribution", card_types)
        self.assertIn("custom:homecave-card", card_types)
        self.assertIn("custom:homecave-sommai-card", card_types)

        blueprint_path = (
            REPO_ROOT
            / "blueprints"
            / "automation"
            / "homecave"
            / "cellar_alerts.yaml"
        )
        blueprint_data = yaml.load(
            blueprint_path.read_text(encoding="utf-8"),
            Loader=_HABlueprintYamlLoader,
        )
        self.assertEqual(blueprint_data["blueprint"]["domain"], "automation")
        self.assertIn("HomeCave", blueprint_data["blueprint"]["name"])
        self.assertIn(
            "https://github.com/Maartenwiese/HomeCave/",
            blueprint_data["blueprint"]["source_url"],
        )

    def test_v091_zero_legacy_brand_remnants_icons_and_docs(self) -> None:
        """Verify zero legacy brand references, zero leaked API keys, valid brand/PWA icons, and generalized AI docs."""
        retired_brand = "lekker" + "sappie"
        forbidden_secret_prefixes = ("sk-ant-" + "usr-", "AQ." + "Ab8RN")

        checked_files = 0
        for file_path in sorted(REPO_ROOT.rglob("*")):
            if ".git" in file_path.parts or "__pycache__" in file_path.parts:
                continue
            rel_str = str(file_path.relative_to(REPO_ROOT))
            self.assertNotIn(
                retired_brand,
                rel_str.lower(),
                f"Legacy brand name found in path: {rel_str}",
            )
            if not file_path.is_file():
                continue
            checked_files += 1
            try:
                text = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            self.assertNotIn(
                retired_brand,
                text.lower(),
                f"Legacy brand reference found inside {rel_str}",
            )
            for secret_prefix in forbidden_secret_prefixes:
                self.assertNotIn(
                    secret_prefix,
                    text,
                    f"Credential prefix '{secret_prefix}' leaked in {rel_str}",
                )

        self.assertGreaterEqual(checked_files, 35)

        # Verify all 25 brand & mobile PNG and SVG assets exist with exact dimensions
        expected_pngs: list[tuple[Path, tuple[int, int]]] = [
            (REPO_ROOT / "brand" / "logo.png", (256, 256)),
            (REPO_ROOT / "brand" / "logo@2x.png", (512, 512)),
            (REPO_ROOT / "brand" / "dark_logo.png", (256, 256)),
            (REPO_ROOT / "brand" / "dark_logo@2x.png", (512, 512)),
            (REPO_ROOT / "brand" / "icon.png", (256, 256)),
            (REPO_ROOT / "brand" / "icon@2x.png", (512, 512)),
            (REPO_ROOT / "brand" / "dark_icon.png", (256, 256)),
            (REPO_ROOT / "brand" / "dark_icon@2x.png", (512, 512)),
            (INTEGRATION_DIR / "icon.png", (256, 256)),
            (INTEGRATION_DIR / "brand" / "logo.png", (256, 256)),
            (INTEGRATION_DIR / "brand" / "logo@2x.png", (512, 512)),
            (INTEGRATION_DIR / "brand" / "dark_logo.png", (256, 256)),
            (INTEGRATION_DIR / "brand" / "dark_logo@2x.png", (512, 512)),
            (INTEGRATION_DIR / "brand" / "icon.png", (256, 256)),
            (INTEGRATION_DIR / "brand" / "icon@2x.png", (512, 512)),
            (INTEGRATION_DIR / "brand" / "dark_icon.png", (256, 256)),
            (INTEGRATION_DIR / "brand" / "dark_icon@2x.png", (512, 512)),
            (INTEGRATION_DIR / "frontend" / "mobile" / "logo-512.png", (512, 512)),
            (INTEGRATION_DIR / "frontend" / "mobile" / "icon-192.png", (192, 192)),
            (INTEGRATION_DIR / "frontend" / "mobile" / "icon-512.png", (512, 512)),
            (INTEGRATION_DIR / "frontend" / "mobile" / "icon-maskable-512.png", (512, 512)),
            (INTEGRATION_DIR / "frontend" / "mobile" / "apple-touch-icon.png", (180, 180)),
        ]
        for png_path, expected_dims in expected_pngs:
            self.assertTrue(png_path.exists(), f"Missing PNG asset: {png_path}")
            self.assertEqual(
                _read_png_dimensions(png_path),
                expected_dims,
                f"Unexpected dimensions for {png_path}",
            )

        expected_svgs = (
            REPO_ROOT / "assets" / "icon.svg",
            REPO_ROOT / "brand" / "icon.svg",
            INTEGRATION_DIR / "brand" / "icon.svg",
            INTEGRATION_DIR / "frontend" / "mobile" / "icon.svg",
        )
        for svg_path in expected_svgs:
            self.assertTrue(svg_path.exists(), f"Missing SVG asset: {svg_path}")
            svg_txt = svg_path.read_text(encoding="utf-8")
            self.assertIn("<svg", svg_txt)
            self.assertIn("#621322", svg_txt)

        # Verify README generalizes AI models and uses HomeCave branding
        readme_txt = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("HomeCave - your local Wine Cellar", readme_txt)
        self.assertIn("https://github.com/Maartenwiese/HomeCave", readme_txt)
        self.assertIn("AI Models", readme_txt)
        self.assertIn("Google Gemini", readme_txt)
        self.assertIn("Anthropic Claude", readme_txt)


if __name__ == "__main__":
    unittest.main()

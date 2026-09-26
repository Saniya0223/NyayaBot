"""Cheap, on-demand diagnostics for the optional Auto-Fill browser runtime."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path


def browser_runtime_status() -> dict[str, bool]:
    browser_use = importlib.util.find_spec("browser_use") is not None
    if browser_use:
        try:
            importlib.import_module("browser_use")
        except Exception:
            browser_use = False
    playwright = importlib.util.find_spec("playwright") is not None
    chromium = False
    if playwright:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as runtime:
                chromium = Path(runtime.chromium.executable_path).is_file()
        except Exception:
            chromium = False
    return {
        "available": browser_use and playwright and chromium,
        "browser_use": browser_use,
        "playwright": playwright,
        "chromium": chromium,
    }

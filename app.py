#!/usr/bin/env python3
"""
Flask back-end for the dividend-calculator front-end.

Project layout
├── backend/
│   └── app.py          ← (this file)
└── frontend/           ← “frontend_static_v2.zip” unzipped here
    ├── index.html
    ├── js/…
    └── css/…

The API now lives under /api/*, while Flask serves the already-built
static assets from the `frontend/` directory.
"""

import os
import re
import logging
import pathlib
from datetime import datetime
from urllib.parse import urljoin   # noqa: F401  (kept for your helpers)

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import yfinance as yf              # noqa: F401  (kept for your helpers)
import requests                    # noqa: F401
from bs4 import BeautifulSoup      # noqa: F401
import pandas as pd                # noqa: F401

# ───────────────────────── static bundle ──────────────────────────
BASE_DIR = pathlib.Path(__file__).parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"          # ‼ adjust if you
                                                     #   keep a different
                                                     #   directory layout

app = Flask(
    __name__,
    static_folder=str(FRONTEND_DIR),
    static_url_path="",              # so “/” maps to index.html
)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# -----------------------------------------------------------------
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-change-me")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────── helper functions (unchanged) ────────────────
def get_most_recent_completed_fy():
    """Return start

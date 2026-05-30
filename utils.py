"""
utils.py — Alfaleus Ingestion Analytics Helpers
Calculates data complexity metrics and structural details prior to HTML stripping.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from models import OpportunityAnalytics

def calculate_text_metrics(raw_html: str, clean_text: str) -> OpportunityAnalytics:
    """
    Calculate text profile, structural density and HTML element metrics.
    Operates before passing text to downstream processing.
    """
    # Clean text metrics
    character_count = len(clean_text)
    word_count = len(clean_text.split())

    # Structural baseline calculations on raw HTML
    html_elements = re.findall(r"<([a-zA-Z1-6]+)(?:\s+[^>]*)?>", raw_html)
    html_element_count = len(html_elements)

    # Specific check for list items (<li> elements)
    list_items = re.findall(r"<li(?:\s+[^>]*)?>", raw_html, re.IGNORECASE)
    list_item_count = len(list_items)

    # HTML element-to-text ratio (structural density)
    text_ratio = round(html_element_count / max(1, character_count), 4)

    return OpportunityAnalytics(
        character_count=character_count,
        word_count=word_count,
        html_element_count=html_element_count,
        text_ratio=text_ratio,
        list_item_count=list_item_count,
        scraped_at=datetime.now(timezone.utc),
        enriched_at=None
    )

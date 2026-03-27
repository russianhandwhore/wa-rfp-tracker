"""
categorize.py — shared RFP category tagging utility
Drop this in your scrapers/ folder and import with:
    from categorize import categorize_rfp

Usage in any scraper:
    rfp['categories'] = categorize_rfp(rfp['title'], rfp['description'])
"""

import re

CATEGORY_KEYWORDS = {
    'IT': [
        r'\bIT\b', r'technology', r'software', r'hardware', r'telecom',
        r'network', r'cyber', r'computer', r'digital', r'\bdata\b',
        r'database', r'cloud', r'fiber', r'wireless', r'internet',
        r'\bweb\b', r'saas', r'server', r'license', r'microsoft',
        r'oracle', r'cisco', r'audiovisual', r'audio.visual', r'\bav\b',
        r'surveillance', r'camera', r'cameras', r'information technology',
        r'system integration', r'managed service', r'helpdesk',
        r'cybersecurity', r'firewall', r'bandwidth', r'hosting',
        r'adobe', r'\bhp\b', r'dell', r'asus', r'lenovo', r'\bibm\b',
        r'scada', r'low voltage', r'physical security', r'security',
        r'\bai\b', r'artificial intelligence', r'machine learning',
    ],
    'Construction': [
        r'construction', r'building', r'renovation', r'repair',
        r'maintenance', r'\broad\b', r'bridge', r'facility',
        r'concrete', r'electrical', r'plumbing', r'hvac', r'roofing',
        r'demolition', r'civil', r'contractor', r'architect',
        r'structural', r'pavement', r'sidewalk', r'trail',
        r'irrigation', r'sewer', r'welding', r'painting',
        r'flooring', r'carpentry', r'masonry', r'grading',
        r'stormwater', r'water main', r'utility installation',
    ],
    'Supplies': [
        r'supplies', r'equipment', r'materials', r'furniture',
        r'vehicle', r'fleet', r'uniform', r'\bgoods\b', r'\bparts\b',
        r'commodity', r'tools', r'machinery', r'fuel', r'chemical',
        r'office supply', r'printing', r'medical supply',
        r'personal protective', r'ppe', r'janitorial supply',
    ],
    'Services': [
        r'services', r'consulting', r'professional', r'staffing',
        r'legal', r'audit', r'marketing', r'training', r'janitorial',
        r'cleaning', r'landscaping', r'grounds', r'security guard',
        r'patrol', r'catering', r'food service', r'translation',
        r'survey', r'inspection', r'testing', r'planning', r'design',
        r'research', r'financial', r'accounting', r'insurance',
        r'waste', r'recycling', r'appraisal', r'environmental',
        r'engineering service', r'architectural service',
    ],
}


def categorize_rfp(title: str, description: str = '') -> list:
    """
    Returns a list of matching category strings from:
    ['IT', 'Construction', 'Supplies', 'Services', 'Misc']

    An RFP can match multiple categories. If none match, returns ['Misc'].
    """
    text = ((title or '') + ' ' + (description or '')).lower()
    matched = []
    for cat, patterns in CATEGORY_KEYWORDS.items():
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            matched.append(cat)
    return matched if matched else ['Misc']

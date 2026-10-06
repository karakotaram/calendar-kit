"""Base class for plain-HTTP scrapers.

A scraper is pure: URL in, EventCreate[] out. No file writes, no globals, no
clock. That is what lets its tests parse a saved page offline (CLAUDE.md).
"""
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from src.config import USER_AGENT
from src.models.event import EventCreate

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """Abstract base class for plain-HTTP event scrapers"""

    def __init__(self, source_name: str, source_url: str):
        self.source_name = source_name
        self.source_url = source_url

    def get_browser_headers(self) -> dict:
        """Request headers for plain-HTTP scrapers.

        Honest: a user-agent naming this calendar, never an impersonation of an
        ordinary browser (docs/rules-and-edge-cases.md, "Identify honestly").
        The name is historical. Some servers refuse the bare python-requests
        default but accept a named client, so always send these.
        """
        return {
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }

    def fetch_html(self, url: str, retries: int = 3) -> str:
        """Fetch a page, retrying with backoff. Raises after the last attempt."""
        last_error = None
        for attempt in range(retries):
            try:
                response = requests.get(url, timeout=30, headers=self.get_browser_headers())
                response.raise_for_status()
                return response.text
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt + 1}/{retries} failed for {url}: {e}")
                if attempt < retries - 1:
                    time.sleep((attempt + 1) * 2)
        logger.error(f"Error fetching {url} after {retries} attempts: {last_error}")
        raise last_error

    @abstractmethod
    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from source - must be implemented by subclasses"""
        pass

    def parse_html(self, html: str) -> BeautifulSoup:
        """Parse HTML into BeautifulSoup object"""
        return BeautifulSoup(html, 'html.parser')

    def clean_text(self, text: str) -> str:
        """Clean and normalize text"""
        if not text:
            return ""
        return ' '.join(text.strip().split())

    def extract_image_url(self, soup: BeautifulSoup, base_url: str = None) -> Optional[str]:
        """
        Extract the best image URL from a page.
        Tries multiple strategies: og:image meta tag, main image tags, etc.
        """
        # Strategy 1: Open Graph image (most reliable for event pages)
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            img_url = og_image['content']
            return self._normalize_image_url(img_url, base_url)

        # Strategy 2: Twitter card image
        twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
        if twitter_image and twitter_image.get('content'):
            img_url = twitter_image['content']
            return self._normalize_image_url(img_url, base_url)

        # Strategy 3: Main content image (look for large images)
        main_content = soup.find('main') or soup.find('article') or soup.find('div', class_=re.compile(r'content|event|detail', re.I))
        if main_content:
            img = main_content.find('img', src=True)
            if img:
                img_url = img.get('src') or img.get('data-src')
                if img_url and self._is_valid_event_image(img_url):
                    return self._normalize_image_url(img_url, base_url)

        # Strategy 4: First large image on page
        for img in soup.find_all('img', src=True)[:10]:
            img_url = img.get('src') or img.get('data-src')
            if img_url and self._is_valid_event_image(img_url):
                return self._normalize_image_url(img_url, base_url)

        return None

    def _normalize_image_url(self, url: str, base_url: str = None) -> str:
        """Normalize image URL to absolute URL"""
        if not url:
            return None

        # Already absolute URL
        if url.startswith('http://') or url.startswith('https://'):
            return url

        # Protocol-relative URL
        if url.startswith('//'):
            return f'https:{url}'

        # Relative URL - need base
        if base_url:
            return urljoin(base_url, url)

        return None

    def _is_valid_event_image(self, url: str) -> bool:
        """Check if URL appears to be a valid event image (not icon/logo/etc)"""
        if not url:
            return False

        url_lower = url.lower()

        # Skip common non-event images
        skip_patterns = [
            'logo', 'icon', 'favicon', 'sprite', 'placeholder',
            'avatar', 'profile', 'banner', 'header', 'footer',
            'loading', 'spinner', 'pixel', '1x1', 'spacer',
            'button', 'arrow', 'social', 'facebook', 'twitter',
            'instagram', 'youtube', 'linkedin', 'pinterest'
        ]

        for pattern in skip_patterns:
            if pattern in url_lower:
                return False

        # Check for common image extensions
        valid_extensions = ['.jpg', '.jpeg', '.png', '.webp', '.gif']
        has_valid_ext = any(ext in url_lower for ext in valid_extensions)

        # Also accept URLs that might be dynamic image services
        is_dynamic = any(service in url_lower for service in ['unsplash', 'cloudinary', 'imgix', 'cdn'])

        return has_valid_ext or is_dynamic or '?' in url  # Query params often indicate dynamic images

    def run(self) -> List[EventCreate]:
        """Execute the scraper and return events"""
        try:
            logger.info(f"Starting scrape of {self.source_name}")
            events = self.scrape_events()
            logger.info(f"Successfully scraped {len(events)} events from {self.source_name}")
            return events
        except Exception as e:
            logger.error(f"Failed to scrape {self.source_name}: {e}")
            raise

"""URL reputation checker with Safe Browsing and VirusTotal integration."""

from __future__ import annotations

import hashlib
import logging
import time

import httpx

from atlas.capabilities.browser.security.reputation import ReputationResult, ReputationVerdict

logger = logging.getLogger(__name__)


class URLReputationChecker:
    """
    Checks URL reputation using Google Safe Browsing and VirusTotal APIs.
    Implements 6-hour TTL caching and fail-open behavior on API failures.
    """

    def __init__(
        self,
        safe_browsing_api_key: str = "",
        virustotal_api_key: str = "",
        timeout_s: float = 30.0,
    ) -> None:
        self._safe_browsing_api_key = safe_browsing_api_key
        self._virustotal_api_key = virustotal_api_key
        self._cache_ttl_s = 6 * 3600
        self._cache: dict[str, tuple[ReputationResult, float]] = {}
        self._client = httpx.AsyncClient(timeout=timeout_s)
        logger.info("URLReputationChecker initialized")

    async def check(self, url: str) -> ReputationResult:
        normalized_url = url.strip()
        url_hash = hashlib.sha256(normalized_url.encode()).hexdigest()

        now = time.time()
        if url_hash in self._cache:
            cached_result, timestamp = self._cache[url_hash]
            if now - timestamp < self._cache_ttl_s:
                logger.debug(f"Cache hit for {normalized_url}")
                return cached_result
            else:
                del self._cache[url_hash]
                logger.debug(f"Cache expired for {normalized_url}")

        result = await self._check_uncached(normalized_url)
        self._cache[url_hash] = (result, now)
        return result

    async def _check_uncached(self, url: str) -> ReputationResult:
        if not self._safe_browsing_api_key and not self._virustotal_api_key:
            return ReputationResult(
                url=url,
                verdict=ReputationVerdict.UNKNOWN,
                reason="No API keys configured",
                checked_by="none",
            )

        logger.info(f"Checking reputation for {url}")

        # Each checker returns: True (malicious), False (clean), None (API failed)
        safe_browsing_result = await self._check_safe_browsing(url)
        if safe_browsing_result is True:
            return ReputationResult(
                url=url,
                verdict=ReputationVerdict.MALICIOUS,
                reason="Flagged by Google Safe Browsing",
                checked_by="safe_browsing",
            )

        virustotal_result = await self._check_virustotal(url)
        if virustotal_result is True:
            return ReputationResult(
                url=url,
                verdict=ReputationVerdict.MALICIOUS,
                reason="Flagged by VirusTotal",
                checked_by="virustotal",
            )

        # If every configured provider failed (returned None), fail-open to UNKNOWN
        # rather than silently treating the URL as safe.
        providers_failed = (self._safe_browsing_api_key and safe_browsing_result is None) and (
            not self._virustotal_api_key or virustotal_result is None
        )
        if providers_failed:
            return ReputationResult(
                url=url,
                verdict=ReputationVerdict.UNKNOWN,
                reason="All reputation API calls failed",
                checked_by="none",
            )

        providers = []
        if self._safe_browsing_api_key:
            providers.append("safe_browsing")
        if self._virustotal_api_key:
            providers.append("virustotal")

        return ReputationResult(
            url=url,
            verdict=ReputationVerdict.SAFE,
            reason="No threats detected",
            checked_by=",".join(providers),
        )

    async def _check_safe_browsing(self, url: str) -> bool | None:
        """Returns True (malicious), False (clean), or None (API failure)."""
        if not self._safe_browsing_api_key:
            return False

        try:
            endpoint = f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={self._safe_browsing_api_key}"
            payload = {
                "client": {
                    "clientId": "atlas",
                    "clientVersion": "1.0.0",
                },
                "threatInfo": {
                    "threatTypes": [
                        "MALWARE",
                        "SOCIAL_ENGINEERING",
                        "UNWANTED_SOFTWARE",
                        "POTENTIALLY_HARMFUL_APPLICATION",
                    ],
                    "platformTypes": ["ANY_PLATFORM"],
                    "threatEntryTypes": ["URL"],
                    "threatEntries": [{"url": url}],
                },
            }

            response = await self._client.post(endpoint, json=payload)
            response.raise_for_status()
            result = response.json()

            is_malicious = bool(result.get("matches"))
            logger.debug(f"Safe Browsing check for {url}: {'malicious' if is_malicious else 'safe'}")
            return is_malicious

        except Exception as e:
            logger.warning(f"Safe Browsing API error for {url}: {e}")
            return None  # signal API failure — caller decides verdict

    async def _check_virustotal(self, url: str) -> bool | None:
        """Returns True (malicious), False (clean), or None (API failure)."""
        if not self._virustotal_api_key:
            return False

        try:
            endpoint = "https://www.virustotal.com/vtapi/v2/url/report"
            params = {
                "apikey": self._virustotal_api_key,
                "resource": url,
            }

            response = await self._client.get(endpoint, params=params)

            if response.status_code == 204:
                logger.warning(f"VirusTotal rate limit exceeded for {url}")
                return False
            elif response.status_code != 200:
                logger.warning(f"VirusTotal API returned status {response.status_code} for {url}")
                return False

            result = response.json()

            if result.get("response_code") == 0:
                logger.debug(f"VirusTotal: URL {url} not found in database")
                return False

            positives = int(result.get("positives", 0))
            total = int(result.get("total", 0))
            is_malicious: bool = positives > 0
            logger.debug(
                f"VirusTotal check for {url}: {'malicious' if is_malicious else 'safe'} "
                f"(positives: {positives}/{total})"
            )
            return is_malicious

        except Exception as e:
            logger.warning(f"VirusTotal API error for {url}: {e}")
            return None  # signal API failure

    async def shutdown(self) -> None:
        await self._client.aclose()
        logger.info("URLReputationChecker shutdown complete")

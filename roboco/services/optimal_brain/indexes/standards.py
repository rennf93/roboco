"""
Standards Index Plugin

Handles indexing and searching coding standards, security policies, and workflow rules.
Standards are stored as markdown files and indexed for semantic search.
"""

import re
from pathlib import Path
from typing import Any

import structlog

from roboco.models.optimal import IndexStandardParams, IndexType, SearchResult, Standard
from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin, IngestResult

logger = structlog.get_logger()


class StandardsIndexPlugin(BaseIndexPlugin):
    """
    Plugin for indexing and searching standards.

    Handles:
    - Coding standards (Python, TypeScript)
    - Security policies (OWASP)
    - Workflow rules (task lifecycle)
    - Architecture guidelines

    Standards are stored as markdown files and indexed for semantic search.
    """

    @property
    def index_type(self) -> IndexType:
        return IndexType.STANDARDS

    def prepare_metadata(
        self,
        content: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Prepare metadata for standard."""
        del content  # Unused - metadata comes from kwargs
        return {
            "type": "standard",
            "domain": kwargs.get("domain", "general"),
            "title": kwargs.get("title", ""),
            "language": kwargs.get("language"),
            "severity": kwargs.get("severity", "recommended"),
            "tags": kwargs.get("tags", []),
            "source_file": kwargs.get("source_file"),
            "rule_id": kwargs.get("rule_id"),
        }

    def build_source_uri(self, doc_id: str | None = None, **kwargs: Any) -> str:
        """Build source URI for standard."""
        domain = kwargs.get("domain", "general")
        rule_id = doc_id or kwargs.get("rule_id", "unknown")
        return f"roboco://standards/{domain}/{rule_id}"

    async def index_standard(self, params: IndexStandardParams) -> IngestResult:
        """
        Index a single standard.

        Args:
            params: IndexStandardParams containing standard details

        Returns:
            IngestResult with ingestion details
        """
        import hashlib

        # Generate rule ID from title if not provided
        title_hash = hashlib.md5(
            params.title.encode(), usedforsecurity=False
        ).hexdigest()[:8]
        rule_id = f"{params.domain[:3]}-{title_hash}"

        # Build searchable content (language/tags in metadata, not text)
        content = f"# {params.title}\n\n{params.content}"

        return await self.ingest(
            content=content,
            doc_id=rule_id,
            domain=params.domain,
            title=params.title,
            language=params.language,
            severity=params.severity,
            tags=params.tags or [],
            source_file=params.source_file,
            rule_id=rule_id,
        )

    async def index_markdown_file(self, file_path: str) -> list[IngestResult]:
        """
        Parse and index a markdown standards file.

        Extracts rules from headers and bullet points.

        Args:
            file_path: Path to the markdown file

        Returns:
            List of IngestResults for each rule indexed
        """
        path = Path(file_path)
        if not path.exists():
            logger.warning(f"Standards file not found: {file_path}")
            return []

        content = path.read_text()

        # Parse domain and language from path
        # Expected format: standards/<domain>/<language>.md
        parts = path.parts
        domain = "general"
        language = None

        if "standards" in parts:
            idx = parts.index("standards")
            if idx + 1 < len(parts):
                domain = parts[idx + 1]
            if idx + 2 < len(parts):
                language = path.stem

        results = []

        # Extract rules from headers and their content
        rules = self._parse_markdown_rules(content)

        for rule in rules:
            params = IndexStandardParams(
                domain=domain,
                title=rule["title"],
                content=rule["content"],
                language=language,
                severity=rule.get("severity", "recommended"),
                tags=rule.get("tags", []),
                source_file=str(path),
            )
            result = await self.index_standard(params)
            results.append(result)

        logger.info(
            f"Indexed {len(results)} rules from {file_path}",
            domain=domain,
            language=language,
        )

        return results

    def _parse_markdown_rules(self, content: str) -> list[dict[str, Any]]:
        """
        Parse markdown content into individual rules.

        Treats each H2 or H3 header as a rule.
        """
        rules = []

        # Split by headers (## or ###)
        sections = re.split(r"^(#{2,3}\s+.+)$", content, flags=re.MULTILINE)

        current_title: str | None = None
        current_content: list[str] = []

        for raw_section in sections:
            section = raw_section.strip()
            if not section:
                continue

            if section.startswith("##"):
                # Save previous rule if exists
                if current_title and current_content:
                    rules.append(
                        {
                            "title": current_title,
                            "content": "\n".join(current_content),
                            "severity": self._detect_severity(
                                current_title, "\n".join(current_content)
                            ),
                            "tags": self._extract_tags(
                                current_title, "\n".join(current_content)
                            ),
                        }
                    )

                # Start new rule
                current_title = section.lstrip("#").strip()
                current_content = []
            else:
                current_content.append(section)

        # Don't forget the last rule
        if current_title and current_content:
            rules.append(
                {
                    "title": current_title,
                    "content": "\n".join(current_content),
                    "severity": self._detect_severity(
                        current_title, "\n".join(current_content)
                    ),
                    "tags": self._extract_tags(
                        current_title, "\n".join(current_content)
                    ),
                }
            )

        return rules

    def _detect_severity(self, title: str, content: str) -> str:
        """Detect severity from keywords."""
        text = (title + " " + content).lower()
        if any(
            word in text for word in ["must", "required", "critical", "never", "always"]
        ):
            return "required"
        if any(word in text for word in ["should", "recommended", "prefer"]):
            return "recommended"
        return "optional"

    def _extract_tags(self, title: str, content: str) -> list[str]:
        """Extract tags from content."""
        tags = []
        text = (title + " " + content).lower()

        # Common tag patterns
        if "security" in text:
            tags.append("security")
        if "performance" in text:
            tags.append("performance")
        if "style" in text:
            tags.append("style")
        if "error" in text or "exception" in text:
            tags.append("error-handling")
        if "test" in text:
            tags.append("testing")
        if "async" in text or "await" in text:
            tags.append("async")

        return tags

    async def get_standards(
        self,
        domain: str,
        language: str | None = None,
        severity: str | None = None,
        top_k: int = 20,
    ) -> list[SearchResult]:
        """
        Get standards for a domain/language.

        Args:
            domain: Domain to search (coding, security, workflow)
            language: Optional language filter
            severity: Optional severity filter

        Returns:
            List of matching standards
        """
        query = f"{domain} standards"
        if language:
            query += f" {language}"

        filters: dict[str, Any] = {"domain": domain}
        if language:
            filters["language"] = language
        if severity:
            filters["severity"] = severity

        outcome = await self.search(query=query, top_k=top_k, filters=filters)
        return outcome.results

    async def validate_against_standards(
        self,
        content: str,
        domain: str,
        language: str | None = None,
    ) -> list[Standard]:
        """
        Find relevant standards for validating content.

        Args:
            content: The content to validate (e.g., code snippet)
            domain: Domain to search
            language: Optional language filter

        Returns:
            List of relevant standards
        """
        # Note: content is used for future relevance filtering
        _ = content[:500]  # Acknowledge parameter usage

        results = await self.get_standards(
            domain=domain,
            language=language,
            top_k=10,
        )

        # Convert to Standard objects
        standards = []
        for result in results:
            standards.append(
                Standard(
                    standard_id=result.metadata.get("rule_id", "unknown"),
                    domain=result.metadata.get("domain", domain),
                    title=result.metadata.get("title", "Unknown"),
                    content=result.content,
                    language=result.metadata.get("language"),
                    severity=result.metadata.get("severity", "recommended"),
                    tags=result.metadata.get("tags", []),
                    source_file=result.metadata.get("source_file"),
                )
            )

        return standards

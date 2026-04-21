"""
Validator Service

Validates agent actions against organizational standards indexed in the
knowledge base. Uses LLM-based analysis to detect violations and provide
actionable feedback.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
import structlog

from roboco.config import settings
from roboco.models.optimal import SearchResult, ValidationResult

logger = structlog.get_logger()

# LLM timeout for validation calls
LLM_TIMEOUT_SECONDS = 60.0

# System prompt for LLM-based validation
VALIDATION_SYSTEM_PROMPT = """You are a code standards validator. Your job is to check if code or actions violate organizational standards.

You will be given:
1. An action type (what the agent is trying to do)
2. Context (the code or content to validate)
3. Relevant standards from the knowledge base

Analyze the context against EACH standard and identify any violations.

IMPORTANT: Be thorough but fair. Only flag actual violations, not stylistic preferences.

Respond in this exact JSON format:
{
    "violations": [
        {
            "rule_id": "RULE-ID from standard",
            "rule_title": "Title of the rule",
            "message": "Clear explanation of what violates the rule",
            "severity": "error|warning|info",
            "line_number": null or line number if applicable,
            "suggestion": "How to fix this violation"
        }
    ],
    "summary": "Brief summary of validation result"
}

If there are no violations, return: {"violations": [], "summary": "No violations found"}

Do NOT include any text outside the JSON. Do NOT use markdown code blocks."""


class RuleSeverity(Enum):
    """Severity levels for rule violations."""

    ERROR = "error"  # Must fix before proceeding
    WARNING = "warning"  # Should fix, but can proceed
    INFO = "info"  # Suggestion for improvement


@dataclass
class ParsedRule:
    """A rule extracted from a standards markdown file."""

    rule_id: str
    title: str
    description: str
    severity: RuleSeverity = RuleSeverity.ERROR
    domain: str = ""
    language: str | None = None
    examples: list[dict[str, str]] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    source_file: str = ""


@dataclass
class Violation:
    """A detected rule violation."""

    rule_id: str
    rule_title: str
    message: str
    severity: RuleSeverity
    line_number: int | None = None
    suggestion: str | None = None


class StandardsParser:
    """
    Parses markdown standards files into structured rules.

    Supports the format:
    ### RULE-ID: Title
    Description text...

    ```language
    # Good/Bad example
    code here
    ```
    """

    # Pattern to match rule headers like "### PY-001: Use Type Hints"
    RULE_HEADER_PATTERN = re.compile(
        r"^###\s+([A-Z]{2,4}-\d{3}):\s+(.+)$", re.MULTILINE
    )

    # Pattern to extract code blocks
    CODE_BLOCK_PATTERN = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)

    def __init__(self, standards_dir: Path | str) -> None:
        """
        Initialize parser with standards directory.

        Args:
            standards_dir: Path to the standards/ directory
        """
        self.standards_dir = Path(standards_dir)

    def parse_file(self, file_path: Path) -> list[ParsedRule]:
        """
        Parse a single markdown file into rules.

        Args:
            file_path: Path to the markdown file

        Returns:
            List of ParsedRule objects
        """
        if not file_path.exists():
            logger.warning("Standards file not found", path=str(file_path))
            return []

        content = file_path.read_text()
        rules: list[ParsedRule] = []

        # Determine domain and language from path
        # e.g., standards/coding/python.md -> domain=coding, language=python
        relative_path = file_path.relative_to(self.standards_dir)
        parts = relative_path.parts
        domain = parts[0] if parts else "general"
        language = file_path.stem if domain == "coding" else None

        # Split content by rule headers
        sections = self.RULE_HEADER_PATTERN.split(content)

        # sections[0] is content before first rule
        # Then pairs of (rule_id, title, content)
        i = 1
        while i < len(sections) - 2:
            rule_id = sections[i]
            title = sections[i + 1]
            # Content goes until next rule or end
            rule_content = sections[i + 2] if i + 2 < len(sections) else ""

            # Extract examples from code blocks
            examples = self._extract_examples(rule_content)

            # Extract keywords from content
            keywords = self._extract_keywords(rule_content, title)

            # Determine severity from content
            severity = self._determine_severity(rule_content)

            rule = ParsedRule(
                rule_id=rule_id,
                title=title.strip(),
                description=self._clean_description(rule_content),
                severity=severity,
                domain=domain,
                language=language,
                examples=examples,
                keywords=keywords,
                source_file=str(file_path),
            )
            rules.append(rule)
            i += 3

        logger.info(
            "Parsed standards file",
            file=str(file_path),
            rules_found=len(rules),
        )
        return rules

    def parse_all(self) -> list[ParsedRule]:
        """
        Parse all markdown files in the standards directory.

        Returns:
            List of all ParsedRule objects
        """
        all_rules: list[ParsedRule] = []

        if not self.standards_dir.exists():
            logger.warning(
                "Standards directory not found",
                path=str(self.standards_dir),
            )
            return all_rules

        for md_file in self.standards_dir.rglob("*.md"):
            rules = self.parse_file(md_file)
            all_rules.extend(rules)

        logger.info(
            "Parsed all standards",
            total_rules=len(all_rules),
            files_processed=len(list(self.standards_dir.rglob("*.md"))),
        )
        return all_rules

    def _extract_examples(self, content: str) -> list[dict[str, str]]:
        """Extract code examples from content."""
        examples = []
        for match in self.CODE_BLOCK_PATTERN.finditer(content):
            lang = match.group(1) or "text"
            code = match.group(2).strip()

            # Determine if good or bad example from preceding text
            preceding = content[: match.start()].split("\n")[-3:]
            preceding_text = "\n".join(preceding).lower()

            example_type = "neutral"
            if "good" in preceding_text or "correct" in preceding_text:
                example_type = "good"
            elif "bad" in preceding_text or "never" in preceding_text:
                example_type = "bad"

            examples.append(
                {
                    "language": lang,
                    "code": code,
                    "type": example_type,
                }
            )
        return examples

    def _extract_keywords(self, content: str, title: str) -> list[str]:
        """Extract searchable keywords from content."""
        # Combine title and first paragraph
        text = f"{title} {content[:500]}".lower()

        # Remove code blocks
        text = self.CODE_BLOCK_PATTERN.sub("", text)

        # Extract significant words
        words = re.findall(r"\b[a-z]{4,}\b", text)

        # Filter common words and deduplicate
        common = {
            "this",
            "that",
            "with",
            "from",
            "have",
            "will",
            "should",
            "must",
            "never",
            "always",
            "example",
            "good",
            "when",
            "before",
        }
        keywords = list({w for w in words if w not in common})

        return keywords[:10]  # Limit to 10 keywords

    def _determine_severity(self, content: str) -> RuleSeverity:
        """Determine rule severity from content."""
        content_lower = content.lower()

        if "must" in content_lower or "never" in content_lower:
            return RuleSeverity.ERROR
        if "should" in content_lower:
            return RuleSeverity.WARNING
        return RuleSeverity.INFO

    def _clean_description(self, content: str) -> str:
        """Clean description by removing code blocks."""
        # Remove code blocks
        cleaned = self.CODE_BLOCK_PATTERN.sub("", content)
        # Remove multiple newlines
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        # Limit length
        max_len = 500
        if len(cleaned) > max_len:
            cleaned = cleaned[:max_len] + "..."
        return cleaned.strip()


class ValidatorService:
    """
    Validates agent actions against organizational standards.

    Uses LLM-based analysis to detect violations, with fallback to
    heuristic pattern matching when LLM is unavailable.
    """

    def __init__(self) -> None:
        """Initialize ValidatorService."""
        self._optimal_service: Any = None
        self._rules_cache: list[ParsedRule] = []
        self._rules_indexed = False
        self._llm_available = True  # Assume available, set False on failure

    async def initialize(
        self,
        optimal_service: Any,
        standards_dir: Path | str | None = None,
    ) -> None:
        """
        Initialize with OptimalService and optionally index standards.

        Args:
            optimal_service: The OptimalService instance
            standards_dir: Optional path to standards directory for indexing
        """
        self._optimal_service = optimal_service

        if standards_dir:
            await self.index_standards(standards_dir)

        # Test LLM connectivity
        await self._test_llm_connectivity()

        logger.info(
            "ValidatorService initialized",
            llm_available=self._llm_available,
        )

    async def _test_llm_connectivity(self) -> None:
        """Test if LLM is available for validation."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{settings.local_llm_base_url}/models")
                self._llm_available = response.is_success
        except Exception as e:
            logger.warning("LLM not available for validation", error=str(e))
            self._llm_available = False

    async def index_standards(self, standards_dir: Path | str) -> int:
        """
        Parse and index all standards from the directory.

        Args:
            standards_dir: Path to the standards/ directory

        Returns:
            Number of rules indexed
        """
        parser = StandardsParser(standards_dir)
        self._rules_cache = parser.parse_all()

        # Index each rule in the STANDARDS index
        indexed_count = 0
        for rule in self._rules_cache:
            try:
                content = self._build_rule_content(rule)
                metadata = {
                    "rule_id": rule.rule_id,
                    "domain": rule.domain,
                    "language": rule.language,
                    "severity": rule.severity.value,
                    "keywords": rule.keywords,
                }
                await self._optimal_service.index_standard(
                    content=content,
                    source=rule.source_file,
                    metadata=metadata,
                )
                indexed_count += 1
            except Exception as e:
                logger.warning(
                    "Failed to index rule",
                    rule_id=rule.rule_id,
                    error=str(e),
                )

        self._rules_indexed = True
        logger.info("Standards indexed", count=indexed_count)
        return indexed_count

    def _build_rule_content(self, rule: ParsedRule) -> str:
        """Build searchable content from a rule."""
        parts = [
            f"Rule {rule.rule_id}: {rule.title}",
            rule.description,
        ]
        if rule.keywords:
            parts.append(f"Keywords: {', '.join(rule.keywords)}")
        return "\n\n".join(parts)

    async def validate(
        self,
        action_type: str,
        context: str,
        language: str | None = None,
    ) -> ValidationResult:
        """
        Validate an action against relevant standards using LLM analysis.

        Args:
            action_type: Type of action (e.g., "write_code", "api_call")
            context: The code or context to validate
            language: Optional language filter

        Returns:
            ValidationResult with violations and warnings
        """
        if not self._optimal_service:
            raise RuntimeError("ValidatorService not initialized")

        # Search for relevant standards
        domain = self._action_to_domain(action_type)
        relevant_standards = await self._optimal_service.get_standards(
            domain=domain,
            language=language,
        )

        if not relevant_standards:
            logger.info(
                "No relevant standards found for validation",
                action_type=action_type,
                domain=domain,
            )
            return ValidationResult(
                allowed=True,
                violations=[],
                warnings=[],
                relevant_standards=[],
            )

        # Use LLM-based validation if available, otherwise fall back to heuristics
        if self._llm_available:
            try:
                violations, warnings = await self._validate_with_llm(
                    action_type=action_type,
                    context=context,
                    standards=relevant_standards,
                )
            except Exception as e:
                logger.warning(
                    "LLM validation failed, using heuristics",
                    error=str(e),
                )
                violations, warnings = self._validate_with_heuristics(
                    context=context,
                    standards=relevant_standards,
                    action_type=action_type,
                )
        else:
            violations, warnings = self._validate_with_heuristics(
                context=context,
                standards=relevant_standards,
                action_type=action_type,
            )

        allowed = len(violations) == 0

        logger.info(
            "Validation complete",
            action_type=action_type,
            allowed=allowed,
            violations=len(violations),
            warnings=len(warnings),
            standards_checked=len(relevant_standards),
        )

        return ValidationResult(
            allowed=allowed,
            violations=violations,
            warnings=warnings,
            relevant_standards=relevant_standards,
        )

    async def _validate_with_llm(
        self,
        action_type: str,
        context: str,
        standards: list[SearchResult],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Use LLM to validate context against standards.

        Returns:
            Tuple of (violations, warnings)
        """
        # Build the standards context for the LLM
        standards_text = self._build_standards_context(standards)

        # Build the user prompt
        user_prompt = f"""ACTION TYPE: {action_type}

CONTEXT TO VALIDATE:
```
{context[:4000]}
```

RELEVANT STANDARDS:
{standards_text}

Analyze the context and identify any violations of the standards above.
Return your analysis as JSON."""

        try:
            async with asyncio.timeout(LLM_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
                    response = await client.post(
                        f"{settings.local_llm_base_url}/chat/completions",
                        json={
                            "model": settings.local_llm_model,
                            "messages": [
                                {"role": "system", "content": VALIDATION_SYSTEM_PROMPT},
                                {"role": "user", "content": user_prompt},
                            ],
                            "max_tokens": 2048,
                            "temperature": 0.1,  # Low temp for consistent analysis
                            "options": {"num_ctx": 8192},
                        },
                    )

                    if response.is_success:
                        data = response.json()
                        raw_response = data["choices"][0]["message"]["content"]
                        return self._parse_llm_response(raw_response)
                    else:
                        logger.warning(
                            "LLM validation call failed",
                            status=response.status_code,
                            error=response.text[:200],
                        )
                        raise RuntimeError(f"LLM call failed: {response.status_code}")

        except TimeoutError:
            logger.warning("LLM validation timed out")
            raise
        except httpx.TimeoutException:
            logger.warning("LLM validation HTTP timeout")
            raise

    def _build_standards_context(self, standards: list[SearchResult]) -> str:
        """Build a formatted string of standards for the LLM."""
        parts = []
        for i, standard in enumerate(standards[:10], 1):  # Limit to 10 standards
            # Extract rule ID if present
            rule_id_match = re.search(r"([A-Z]{2,4}-\d{3})", standard.content)
            rule_id = rule_id_match.group(1) if rule_id_match else f"RULE-{i:03d}"

            parts.append(f"--- Standard {rule_id} ---")
            parts.append(standard.content[:800])  # Limit each standard
            parts.append("")

        return "\n".join(parts)

    def _parse_llm_response(
        self,
        raw_response: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Parse LLM response JSON into violations and warnings.

        Returns:
            Tuple of (violations, warnings)
        """
        violations: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        # Clean up the response - remove markdown code blocks if present
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            # Remove ```json and ``` markers
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        # Also handle <think> tags from some models
        cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL)
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning(
                "Failed to parse LLM validation response",
                error=str(e),
                response_preview=cleaned[:200],
            )
            return [], []

        # Extract violations from response
        for item in data.get("violations", []):
            entry = {
                "rule_id": item.get("rule_id", "UNKNOWN"),
                "rule_title": item.get("rule_title", "Unknown Rule"),
                "message": item.get("message", "Violation detected"),
                "severity": item.get("severity", "error"),
                "line_number": item.get("line_number"),
                "suggestion": item.get("suggestion"),
            }

            severity = entry["severity"].lower()
            if severity == "error":
                violations.append(entry)
            else:
                warnings.append(entry)

        return violations, warnings

    def _validate_with_heuristics(
        self,
        context: str,
        standards: list[SearchResult],
        action_type: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Fallback heuristic-based validation when LLM is unavailable.

        Returns:
            Tuple of (violations, warnings)
        """
        violations: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        for standard in standards:
            violation = self._check_rule(standard, context, action_type)
            if violation:
                entry = {
                    "rule_id": violation.rule_id,
                    "rule_title": violation.rule_title,
                    "message": violation.message,
                    "severity": violation.severity.value,
                    "line_number": violation.line_number,
                    "suggestion": violation.suggestion,
                }
                if violation.severity == RuleSeverity.ERROR:
                    violations.append(entry)
                else:
                    warnings.append(entry)

        return violations, warnings

    def _action_to_domain(self, action_type: str) -> str:
        """Map action type to standards domain."""
        action_lower = action_type.lower()

        if any(kw in action_lower for kw in ["code", "function", "class"]):
            return "coding"
        if any(kw in action_lower for kw in ["auth", "secret", "password"]):
            return "security"
        if any(kw in action_lower for kw in ["task", "workflow", "status"]):
            return "workflow"

        return "coding"  # Default

    def _check_rule(
        self,
        standard: SearchResult,
        context: str,
        _action_type: str,
    ) -> Violation | None:
        """
        Check if context violates a standard.

        This is a heuristic check. For production, integrate with LLM.
        """
        content_lower = context.lower()
        rule_content = standard.content.lower()

        # Extract rule ID from standard
        rule_id_match = re.search(r"([A-Z]{2,4}-\d{3})", standard.content)
        rule_id = rule_id_match.group(1) if rule_id_match else "UNKNOWN"

        # Extract title
        title_match = re.search(r"Rule [A-Z]+-\d+: (.+?)(?:\n|$)", standard.content)
        title = title_match.group(1) if title_match else "Unknown Rule"

        # Check for "NEVER" patterns
        never_patterns = re.findall(r"never\s+(\w+(?:\s+\w+){0,3})", rule_content)
        for pattern in never_patterns:
            if pattern in content_lower:
                return Violation(
                    rule_id=rule_id,
                    rule_title=title,
                    message=f"Violation: Should never {pattern}",
                    severity=RuleSeverity.ERROR,
                    suggestion=f"Avoid: {pattern}",
                )

        # Check for missing "MUST" patterns
        must_patterns = re.findall(r"must\s+(\w+(?:\s+\w+){0,3})", rule_content)
        for pattern in must_patterns:
            # This is a simplified check - in production use LLM
            if "type hint" in pattern and "def " in context and "->" not in context:
                return Violation(
                    rule_id=rule_id,
                    rule_title=title,
                    message="Missing return type hint",
                    severity=RuleSeverity.ERROR,
                    suggestion="Add return type annotation",
                )

        return None

    async def get_rules_for_domain(
        self,
        domain: str,
        language: str | None = None,
    ) -> list[ParsedRule]:
        """
        Get all cached rules for a domain.

        Args:
            domain: The domain to filter by
            language: Optional language filter

        Returns:
            List of matching rules
        """
        rules = [r for r in self._rules_cache if r.domain == domain]
        if language:
            rules = [r for r in rules if r.language == language]
        return rules


class _ValidatorServiceHolder:
    """Holder for singleton ValidatorService instance."""

    instance: ValidatorService | None = None


async def get_validator_service() -> ValidatorService:
    """Get or create the ValidatorService instance."""
    if _ValidatorServiceHolder.instance is None:
        _ValidatorServiceHolder.instance = ValidatorService()
    return _ValidatorServiceHolder.instance

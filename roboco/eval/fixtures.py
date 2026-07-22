"""Golden-task fixtures for the eval bench (see ``roboco/eval/runner.py``).

Each ``BenchTaskSpec`` is a tiny, self-contained "golden task": a few
pre-seeded repo files, a task brief (title/description/acceptance criteria),
and a checked-in ``expectations`` note the local-model judge grades the final
PR diff + dev notes against. Fixture repo files are namespaced under
``bench/<key>/`` so every fixture can share one disposable project's git
history without colliding with the others (the runner seeds fixtures onto
the same project's default branch, one at a time).
"""

from __future__ import annotations

from dataclasses import dataclass

from roboco.models.base import TaskNature, TaskType


@dataclass(frozen=True)
class BenchTaskSpec:
    """One golden task: seeded repo state + brief + graded expectation.

    ``target_role`` is always ``"developer"`` — the only role a task can be
    freshly assigned to from PENDING with no prior work already done (QA /
    documenter / cell-PM only ever pick up a task a developer has already
    advanced through the lifecycle). Kept as an explicit field rather than
    hardcoded at the call site so a future QA/PM-focused bench fixture has
    somewhere to say otherwise.
    """

    key: str
    title: str
    description: str
    acceptance_criteria: tuple[str, ...]
    task_type: TaskType
    nature: TaskNature
    repo_files: tuple[tuple[str, str], ...]
    expectations: str
    target_role: str = "developer"


FIXTURES: tuple[BenchTaskSpec, ...] = (
    BenchTaskSpec(
        key="bugfix-off-by-one",
        title="Fix off-by-one in paginate()",
        description=(
            "`bench/bugfix-off-by-one/paginate.py`'s `paginate(items, page, "
            "size)` drops the last item of every page because its slice end "
            "is `page * size - 1` instead of `page * size`. Fix the slice "
            "bound so every item appears exactly once across all pages."
        ),
        acceptance_criteria=(
            "paginate(list(range(10)), page=1, size=3) returns [0, 1, 2]",
            "paginate(list(range(10)), page=4, size=3) returns [9] (the "
            "last, previously-dropped item)",
            "No item is duplicated or skipped across pages 1..4 for size=3",
        ),
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        repo_files=(
            (
                "bench/bugfix-off-by-one/paginate.py",
                "def paginate(items, page, size):\n"
                "    start = (page - 1) * size\n"
                "    end = page * size - 1\n"
                "    return items[start:end]\n",
            ),
        ),
        expectations=(
            "The fix changes the slice end to `page * size` (or an "
            "equivalent that includes the final item). A correct diff "
            "touches only paginate.py's slice bound; no new dependency, no "
            "unrelated rewrite. Commit/PR notes should describe the "
            "off-by-one root cause, not just 'fixed a bug'."
        ),
    ),
    BenchTaskSpec(
        key="bugfix-null-check",
        title="Fix crash on empty input in summarize()",
        description=(
            "`bench/bugfix-null-check/stats.py`'s `summarize(values)` "
            "divides by `len(values)` unconditionally, so it raises "
            "ZeroDivisionError on an empty list instead of returning a "
            "sane empty-input result. Add a guard."
        ),
        acceptance_criteria=(
            "summarize([]) returns {'count': 0, 'total': 0, 'average': 0} "
            "without raising",
            "summarize([2, 4, 6]) still returns "
            "{'count': 3, 'total': 12, 'average': 4}",
        ),
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        repo_files=(
            (
                "bench/bugfix-null-check/stats.py",
                "def summarize(values):\n"
                "    total = sum(values)\n"
                "    count = len(values)\n"
                "    return {\n"
                "        'count': count,\n"
                "        'total': total,\n"
                "        'average': total / count,\n"
                "    }\n",
            ),
        ),
        expectations=(
            "The fix adds an explicit empty-input guard (e.g. `if not "
            "values: return {...}`) ahead of the division, without "
            "changing the non-empty behavior. No new dependency; the "
            "guard is the whole diff."
        ),
    ),
    BenchTaskSpec(
        key="small-feature-greet",
        title="Add a greet() helper",
        description=(
            "`bench/small-feature-greet/greetings.py` has no greeting "
            "helper yet. Add a `greet(name, formal=False)` function: "
            "informal returns `f'Hi, {name}!'`, formal returns "
            "`f'Good day, {name}.'`. Empty/whitespace-only `name` should "
            "raise `ValueError`."
        ),
        acceptance_criteria=(
            "greet('Ada') == 'Hi, Ada!'",
            "greet('Ada', formal=True) == 'Good day, Ada.'",
            "greet('') and greet('   ') both raise ValueError",
        ),
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        repo_files=(
            (
                "bench/small-feature-greet/greetings.py",
                "# Greeting helpers for bench/small-feature-greet.\n",
            ),
        ),
        expectations=(
            "greet() is added to greetings.py matching both the informal "
            "and formal wording exactly, plus the empty-name ValueError "
            "guard. A minimal, additive diff — no unrelated changes to "
            "the file's header comment."
        ),
    ),
    BenchTaskSpec(
        key="refactor-duplicate-normalize",
        title="De-duplicate normalize_a / normalize_b",
        description=(
            "`bench/refactor-duplicate-normalize/normalize.py` has two "
            "near-identical functions, `normalize_a` and `normalize_b` — "
            "both strip whitespace and lowercase a string, differing only "
            "in which module used to call them. Refactor into one shared "
            "helper both call, preserving both public names as thin "
            "wrappers so existing callers are unaffected."
        ),
        acceptance_criteria=(
            "normalize_a('  Hello ') == 'hello'",
            "normalize_b('  Hello ') == 'hello'",
            "The duplicated strip/lower logic exists in exactly one place",
        ),
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        repo_files=(
            (
                "bench/refactor-duplicate-normalize/normalize.py",
                "def normalize_a(text):\n"
                "    return text.strip().lower()\n"
                "\n"
                "\n"
                "def normalize_b(text):\n"
                "    return text.strip().lower()\n",
            ),
        ),
        expectations=(
            "A single private helper (e.g. `_normalize`) holds the "
            "strip/lower logic; normalize_a/normalize_b both delegate to "
            "it and keep their existing signatures and return values "
            "identical to before. No behavior change, pure de-duplication."
        ),
    ),
    BenchTaskSpec(
        key="docs-readme-flag",
        title="Document the --dry-run flag",
        description=(
            "`bench/docs-readme-flag/cli.py` accepts a `--dry-run` flag "
            "(prints what it would do instead of doing it) that isn't "
            "mentioned anywhere in `bench/docs-readme-flag/README.md`. Add "
            "a short section documenting it: what it does and an example "
            "invocation."
        ),
        acceptance_criteria=(
            "README.md documents --dry-run's behavior in prose",
            "README.md shows an example command line using --dry-run",
        ),
        task_type=TaskType.DOCUMENTATION,
        nature=TaskNature.TECHNICAL,
        repo_files=(
            (
                "bench/docs-readme-flag/cli.py",
                "import argparse\n"
                "\n"
                "\n"
                "def build_parser():\n"
                "    parser = argparse.ArgumentParser()\n"
                "    parser.add_argument('--dry-run', action='store_true')\n"
                "    return parser\n",
            ),
            (
                "bench/docs-readme-flag/README.md",
                "# bench/docs-readme-flag\n\nA tiny CLI fixture.\n",
            ),
        ),
        expectations=(
            "README.md gains a section documenting --dry-run's actual "
            "behavior (prints instead of acting) with a runnable example "
            "invocation. cli.py itself is unchanged — this is a docs-only "
            "task."
        ),
    ),
    BenchTaskSpec(
        key="research-magic-constant",
        title="Research the magic constant in legacy_calc.py",
        description=(
            "`bench/research-magic-constant/legacy_calc.py`'s `compute()` "
            "multiplies by `1.10000001` instead of the obvious `1.1`. "
            "Investigate the surrounding code/comments for why, and commit "
            "your findings as `bench/research-magic-constant/NOTES.md` — "
            "this is a research task, not a code fix: legacy_calc.py stays "
            "unchanged."
        ),
        acceptance_criteria=(
            "NOTES.md exists and explains what the magic constant is "
            "compensating for, based on the evidence in the file",
            "NOTES.md recommends whether it's safe to simplify to 1.1, with reasoning",
            "legacy_calc.py is not modified",
        ),
        task_type=TaskType.RESEARCH,
        nature=TaskNature.TECHNICAL,
        repo_files=(
            (
                "bench/research-magic-constant/legacy_calc.py",
                "# The 1.10000001 factor below is NOT a typo for 1.1 — it\n"
                "# nudges the float rounding in compute()'s downstream\n"
                "# int(...) truncation so historical invoice totals ending\n"
                "# in .10 don't get truncated to one cent short. Changing\n"
                "# this constant reopens ROBO-lore ticket #4471 (pre-git\n"
                "# history) where 1.1 exactly caused a cent-level\n"
                "# reconciliation mismatch on ~0.3% of invoices.\n"
                "def compute(amount):\n"
                "    return int(amount * 1.10000001)\n",
            ),
        ),
        expectations=(
            "NOTES.md correctly identifies (from the file's own comment) "
            "that the constant compensates for float-truncation rounding "
            "in compute()'s int(...) cast, cites the historical "
            "reconciliation-mismatch reasoning, and recommends AGAINST "
            "simplifying to 1.1 without a broader fix — it should not "
            "invent an unrelated explanation, and legacy_calc.py itself "
            "must be untouched."
        ),
    ),
)

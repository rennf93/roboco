# Status Transitions

| Source | Target | Action | Roles |
|--------|--------|--------|-------|
| awaiting_ceo_approval | cancelled | cancel | cell_pm, ceo, main_pm |
| awaiting_ceo_approval | completed | ceo_approve | ceo |
| awaiting_ceo_approval | needs_revision | ceo_reject | ceo |
| awaiting_documentation | awaiting_pm_review | docs_complete | documenter |
| awaiting_documentation | cancelled | cancel | cell_pm, ceo, main_pm |
| awaiting_documentation | claimed | claim | documenter |
| awaiting_pm_review | awaiting_ceo_approval | escalate_to_ceo | head_marketing, main_pm, product_owner |
| awaiting_pm_review | cancelled | cancel | cell_pm, ceo, main_pm |
| awaiting_pm_review | completed | complete | cell_pm, main_pm |
| awaiting_qa | awaiting_documentation | qa_pass | qa |
| awaiting_qa | cancelled | cancel | cell_pm, ceo, main_pm |
| awaiting_qa | claimed | claim | qa |
| awaiting_qa | needs_revision | qa_fail | qa |
| backlog | cancelled | cancel | cell_pm, ceo, main_pm |
| backlog | pending | activate | any |
| blocked | awaiting_ceo_approval | escalate_to_ceo | head_marketing, main_pm, product_owner |
| blocked | cancelled | cancel | cell_pm, ceo, main_pm |
| blocked | in_progress | unblock | any |
| blocked | pending | unblock | any |
| claimed | cancelled | cancel | cell_pm, ceo, main_pm |
| claimed | in_progress | start | any |
| in_progress | awaiting_pm_review | submit_pm_review | any |
| in_progress | blocked | block | any |
| in_progress | cancelled | cancel | cell_pm, ceo, main_pm |
| in_progress | completed | pr_review_done | pr_reviewer |
| in_progress | paused | pause | any |
| in_progress | verifying | submit_verification | any |
| needs_revision | cancelled | cancel | cell_pm, ceo, main_pm |
| needs_revision | claimed | claim | any |
| paused | cancelled | cancel | cell_pm, ceo, main_pm |
| paused | in_progress | resume | any |
| pending | cancelled | cancel | cell_pm, ceo, main_pm |
| pending | claimed | claim | any |
| verifying | awaiting_qa | submit_qa | any |
| verifying | cancelled | cancel | cell_pm, ceo, main_pm |

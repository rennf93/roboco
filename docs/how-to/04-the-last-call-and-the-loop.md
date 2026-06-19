# The last call — and the loop

## 6 · The last call is yours

The cells' work is folded up, the Main PM opens the **final pull request** into `master`, and the company goes quiet. The decision comes back to exactly where it started — with you. You're the only one who ever touches `master`, and anything waiting on you sits in the **CEO Approval Queue** until you act.

![The final CEO approval notification: the integrated Prompter PR is ready, all three cells delivered and QA-passed, awaiting the CEO's review and merge.](../images/ceo_approval_notif.png)

*The hand-off back to you. The integrated PR is open, every cell has delivered, QA is green — and it waits. Nothing reaches `master` without your word.*

![The integrated pull request open on GitHub, in the repository's Pull Requests list.](../images/opened_final_pr.png)

*And it is a real pull request, on the real repository — not a simulation. The company's work shows up exactly where any engineer would look for it.*

![The pull request's description: the objective, what the task builds, the per-cell breakdown, and the notes the company wrote for it.](../images/opened_final_pr_body.png)

*Open it and the whole brief is there — the objective, what was built, the board-led split across the three cells, and the company's own notes — written by RoboCo, for you to read before you decide.*

![The pull request's Files changed tab: the actual diff — migrations, API, and panel components — the company is asking to merge.](../images/opened_final_pr_changes.png)

*The real diff, laid out for you to inspect — the migrations, the endpoints, the panel components. This is the substance you are signing off on.*

![The CEO's actions on the awaiting-approval task: Approve & Merge, Request Changes, or Cancel.](../images/approve_button_merge_rework.png)

*Your two words. **Approve & Merge** and it ships to `master`; **Request Changes** and it goes around for another lap. The last call has the same shape as the first — one decision, yours alone.*

## The other queue: PRs you didn't open

Not every pull request comes from inside the company. When someone opens a PR against your repo — an external contributor, a fork — the read-only **PR Reviewer** picks it up, reads the diff against your standards, and posts a single change-request directly on the PR (it never chats, never merges, never decides). The PR then surfaces in the **PR Review Queue** on the Command Center — your second decision surface. There you **Supersede** it: the company cuts its own branch from the contributor's commits, hardens the work to your standards, opens its own PR, and — once that replacement merges — closes and links the original. Or you **Dismiss** it. Either way the call is yours, and the org never pushes to anyone else's fork.

---

## And round it goes

You handed the company a task; it scoped it, built it, failed and re-ran its own QA, documented it, and brought it back as a single pull request for your sign-off. That's one complete pass.

![The full task table for the Prompter feature: a parent task awaiting CEO approval over its completed UX/UI, Frontend, and Backend child tasks.](../images/all_tasks_final_state.png)

*The whole tree in its final state — the parent waiting on your approval, every cell's task done beneath it. One feature, start to finish, with you at only the two ends.*

And the feature in these screenshots is the proof. The **Prompter** wasn't built for a demo — it's a real page RoboCo's agents shipped to RoboCo's own control panel. A company building its own product, in front of you, is the whole point of RoboCo. What makes that hold together isn't a clever model or a lucky run; it's the **organization** — the roles, the gated lifecycle, the reviews and the sign-offs that keep twenty-two agents moving as a company instead of a crowd. Run as many of these passes as you like, across as many projects as you like.

---

Previous: **[← The cells build it](03-the-cells-build-it.md)** · Next: **[The business workflow →](05-the-business-workflow.md)**

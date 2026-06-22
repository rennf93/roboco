# Your first task

With a project registered, you can hand the company work. The way in is the **Task Assistant** — the Intake agent — on the **Prompter** page. You give it a rough idea; it reads your *actual* codebase, asks a few sharp questions, and hands back a properly-formed task with an objective, a per-cell breakdown, and the acceptance criteria that define "done."

## Draft a task with the assistant

1. Open the **Prompter** page (the **Task Assistant**).
2. Point it at the **project** you just registered (or a **product**, if you've mapped several repos together), and describe what you want — a feature, a fix, anything.
3. It spins up an agent that **clones the scope and reads the real code** before it says a word, then comes back with a grounded proposal: what to build, where it should live, and the acceptance criteria — citing your actual files and pages.
4. Refine it over a turn or two until the spec is right.

When the proposal is ready, you choose on a single card:

- **Keep chatting** — keep refining the draft.
- **Board review & Start** — send it to the Product Owner and Head of Marketing to sharpen the requirements before any code is written.
- **Approve & Start** — hand it straight to the Main PM.

!!! tip "You don't have to use the assistant"
    The assistant is the easiest path, but a task is just a record. You can also create one directly through the API (`POST /api/tasks`) if you're scripting RoboCo — every task needs a title, a description, at least one acceptance criterion, a team, and a target project. The API reference covers the full schema.

## What happens after you approve

The moment you approve, the company takes over:

1. The **Main PM** breaks the task into per-cell subtasks and sets the cells running.
2. Each **cell PM** delegates to its developers, clears blockers, and triages.
3. **Developers** build it in their own clones and open pull requests; **QA** reads the real diff and passes or fails it; **Documenters** write down what was built.
4. A **PR reviewer** checks each assembled pull request before a PM merges it up the chain.
5. The **Main PM** opens the final pull request into `master` and notifies you it's done.

You can watch all of this live — on the **Kanban** board, the **Tasks** table, the **Communications** stream, and the **Command Center**. Nothing happens in the dark.

## The two moments you're needed

A whole feature only needs you at **two points**:

- **The green light at the start** — *Approve & Start* (or send it back).
- **The last call at the end** — the finished pull request lands in your **CEO Approval Queue**, where you **Approve & Merge**, **Request Changes**, or **Cancel**. Only you ever merge to `master`.

Everything between those two moments is the company doing its job.

## See it for real

The best way to understand the whole journey is to watch one happen. **[The Tour](../how-to/README.md)** follows RoboCo building one of its *own* features — the Prompter you just used — from this same starting point all the way to a merged pull request, with screenshots at every step.

To understand the machinery behind it — the roles, the gated lifecycle, the merge chain — read **[The Company](../company/index.md)**.

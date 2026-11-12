"""The 1.2.7 autonomous-agent core: evidence, independent verification, deep research.

Nothing in this package re-implements a tool, a task row or the state machine. It adds the three
things the previous architecture did not have:

* a *record* of what was actually observed (`evidence`), separate from what the model says;
* an independent *verifier* that re-checks the user's acceptance criteria against the machine;
* an adaptive research loop whose next query is derived from the fact that is still missing.

A model can therefore claim "the app runs"; only evidence makes the task complete.
"""

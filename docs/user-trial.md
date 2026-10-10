# Five-person trial

Recruiting is deferred. No invitations have been sent and no user results
have been collected. Use this exercise when five volunteers are available.

Ask people who use Python notebooks regularly, across JupyterLab, VS Code and
Colab where practical. Use a notebook they can share safely; no private data
or code needs to be sent to the maintainer.

## Exercise (about five minutes)

1. Install notebookproof inside their existing notebook environment.
2. Run `examples/quickstart.ipynb`. Explain what the reported issues mean.
3. Save one of their own notebooks and run `check("their-notebook.ipynb")`.
   If they intend to execute it, install `notebookproof[verify]` and run
   `check("their-notebook.ipynb", run=True)`. Identify a changed output or an
   explicitly disclosed comparison gap. Do not share the private notebook.
4. Run `frame(df)` on a DataFrame they already use. Declare a key only if it
   should really be unique. Declare one expected nullable/unique/range rule
   and locate any affected row using the reported row positions.
5. Identify one useful finding or one false alarm. Decide whether a suggested
   next step is clear enough to carry out.
6. Save the local HTML report. Check whether they can understand it and decide
   whether it is safe to share; it contains names and finding descriptions.

The optional `verify()` check executes notebook code and its side effects.
Use only a notebook the volunteer intends to execute; it is not required for
this first trial.

## Questions

- What notebook environment and Python version did you use?
- How many minutes until the first useful result? Where did you get stuck?
- Did it catch anything you had missed? Describe it without private data.
- Which findings were wrong, distracting, or unclear?
- Was the difference between static checking and fresh-kernel execution clear?
- Were changed outputs distinguished from wrong results and missing baselines?
- Did declared rules reduce noise and make the affected rows easy to locate?
- Was the HTML report useful without having to inspect the returned dictionary?
- Would you run this before sharing a notebook or reporting a number? Why?

## Follow-up

After one week, ask whether they ran it again without prompting. Record actual
use, not just enthusiasm. Prioritise repeated setup problems and false alarms
before expanding features.

| Participant | Environment | Minutes to value | Useful finding | False alarms | Used again after a week |
|---|---|---|---|---|---|
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |

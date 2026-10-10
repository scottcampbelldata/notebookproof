# Publish notebookproof

Release files are built and checked by `.github/workflows/publish.yml`.
The workflow runs on a published GitHub release or a manual dispatch and uses
PyPI Trusted Publishing. Base installation has no required dependencies.

## One-time PyPI setup

Sign in to the owner account, verify its email, enable 2FA, and safely retain
recovery codes. In account **Publishing**, add a pending GitHub publisher:

| Field | Value |
|---|---|
| PyPI project name | `notebookproof` |
| GitHub owner | `scottcampbelldata` |
| Repository | `notebookproof` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

After the first upload, this becomes the project's normal trusted publisher.
Registering a pending publisher does not reserve the package name.

## Release

1. Keep `pyproject.toml` and `notebookproof.__version__` equal.
2. Run the complete test suite, build the wheel and source distribution, and
   run `twine check`. Confirm CI passes on the exact release commit.
3. Publish a GitHub release tagged with that version, for example `v0.2.0`.
4. Confirm the publishing workflow succeeds and PyPI lists the intended version.
5. Install that version from PyPI in a fresh environment and import it. Execute
   the quickstart in a real notebook kernel before claiming the release works.
6. Change README installation instructions to `%pip install notebookproof`
   only after the PyPI release is verified.

For a failed upload, inspect the job log and correct the setup before manually
rerunning the publishing workflow. Do not claim PyPI availability from a GitHub
push or a locally built wheel alone.

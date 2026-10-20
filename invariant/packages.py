"""Project-local Python installs; never add dependencies to the trusted host."""

DIRECTORY = '.invariant-deps'
USER_BASE = '/workspace/' + DIRECTORY + '/python'
# Keep system commands first: an installed script must not shadow the executor
# or the Python interpreter used by trusted, isolated inspection probes.
ENVIRONMENT = {
    'PYTHONUSERBASE': USER_BASE,
    'PIP_CACHE_DIR': '/workspace/' + DIRECTORY + '/cache/pip',
    'PATH': '/usr/local/bin:/usr/bin:/bin:' + USER_BASE + '/bin',
}


def instructions(policy):
    common = ('Saved Python packages are automatically available on run and resume, including offline. '
              'Their command-line tools are on PATH. Dependencies stay inside this project, never in the host Python. '
              'System files are read-only; sudo and system package installation are unavailable. ')
    if policy['workspace_access'] == 'read-only':
        return (common + 'The project and saved dependencies are read-only. To install a temporary Python package, '
                'use PYTHONUSERBASE=/scratch/.local python3 -m pip install --user PACKAGE and set '
                'PYTHONUSERBASE=/scratch/.local when using it; those temporary packages disappear on exit. '
                'Persistent installation requires the operator to select read-write access and launch a new session. ')
    return (common + 'Install Python packages with python3 -m pip install PACKAGE (or --user); '
            'PYTHONUSERBASE is ' + USER_BASE + ', so packages and their command-line tools persist automatically. '
            'Do not create a virtual environment: project symlinks are rejected on the next launch. '
            'Explicit --target installs still require the corresponding PYTHONPATH. ')

"""Stable errors shared by human and structured callers."""


class ExperimentError(RuntimeError):
    def __init__(self, code, message, recovery='Correct the input and retry.', *, details=None):
        super().__init__(message)
        self.code = code
        self.recovery = recovery
        self.details = details or {}

    def record(self):
        return {'code': self.code, 'message': str(self), 'recovery': self.recovery,
                'details': self.details}


def recovery_for(code):
    """Concrete actions never imply an automatic replay of an uncertain attempt."""
    if code in ('AUTH_REQUIRED', 'AUTH_FAILED'):
        return 'Restore the host Codex ChatGPT sign-in, then explicitly retry the failed trial. Experiments never open a login prompt.'
    if code == 'SECRET_UNAVAILABLE':
        return 'Restore the named private credential-file reference, then explicitly retry the failed trial.'
    if code in ('MODEL_UNAVAILABLE', 'MODEL_MISMATCH', 'EFFORT_UNSUPPORTED', 'MODEL_CONTEXT_LIMIT'):
        return 'Inspect the frozen model/effort and native evidence. Restore that model or create a new study with supported settings; no fallback is substituted.'
    if code == 'CODEX_UNAVAILABLE':
        return 'Run invariant setup on the host, then explicitly retry the failed trial.'
    if code in ('RUNTIME_UNAVAILABLE', 'RUNTIME_MISMATCH', 'IMAGE_UNAVAILABLE'):
        return 'Start Docker and restore the exact frozen image; recover the study before executing queued work.'
    if code in ('CAPACITY_EXCEEDED', 'OOM'):
        return 'Free Docker capacity or increase Docker resources. A new recipe/study is required to change frozen trial limits.'
    if code in ('DISK_HEADROOM', 'CAPTURE_FAILED', 'CHECKPOINT_FAILED'):
        return 'Free disk space and recover this study. Retained workspaces/stopped containers and prior checkpoints are kept.'
    if code in ('CORRUPT_STATE', 'BASELINE_CHANGED', 'INPUT_CHANGED', 'SOURCE_CHANGED'):
        return 'Restore the original frozen files or create a new study; current project files are never substituted.'
    if code in ('THROTTLED', 'RATE_LIMITED'):
        return 'Wait for provider capacity, then explicitly retry. Interrupted calls are never silently replayed.'
    if code and ('EVALUATION' in code or code == 'OUTPUT_LIMIT'):
        return 'Inspect the saved evaluator output. Retry unchanged inputs explicitly, or create a new study with a corrected evaluator.'
    if code in ('UNRESOLVED_EXECUTION', 'RECOVERY_UNRESOLVED', 'CONTROLLER_INTERRUPTED',
                'CONTROLLER_LOST', 'CONTROLLER_FAILED', 'CONTROLLER_RECOVERY_REQUIRED', 'RECOVERY_REQUIRED'):
        return 'Restore Docker connectivity and recover STUDY_ID. Retry interrupted trials explicitly after process stop is confirmed.'
    return 'Inspect saved logs and evidence; use retry TRIAL_ID for another attempt with the same frozen inputs.'

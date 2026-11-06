"""Stable operation stages and diagnostic codes; never classify exception text."""
import traceback

SCHEMA = 'ecm-operation-diagnostics-v2'
REVISION = 'root-install-flow-20261030-r1'
STAGES = {
    'startup': ('Create operation record', 'LOG_UNAVAILABLE'),
    'package_check': ('Select and validate package', 'PACKAGE_INVALID'),
    'environment_check': ('Observe and compare environment', 'ENVIRONMENT_CHECK_FAILED'),
    'confirmation': ('User confirmation', 'CONFIRMATION_FAILED'),
    'authentication': ('Run one confirmed privileged installation', 'AUTH_OR_EXECUTOR_FAILED'),
    'transaction_precheck': ('Lock and verify transaction prerequisites', 'PRECHECK_FAILED'),
    'backup': ('Preserve original files and prepare immutable plan', 'BACKUP_FAILED'),
    'takeover': ('Prepare selected camera module replacements and loaders', 'MODULE_RENAME_CONFLICT'),
    'file_write': ('Write and verify package files', 'FILE_WRITE_FAILED'),
    'boot_config': ('Write and verify boot configuration', 'BOOT_CONFIG_FAILED'),
    'depmod': ('Update dependency indexes', 'DEPMOD_FAILED'),
    'module_path_check': ('Verify selected module paths', 'MODULE_PATH_CHECK_FAILED'),
    'result': ('Save installed state and operation result', 'RESULT_PERSIST_FAILED'),
    'recovery': ('Basic installation-failure recovery', 'RECOVERY_FAILED'),
}
CODES = {
    'BACKUP_INCOMPLETE': 'Some original files could not be saved due to insufficient space. Full restoration is unavailable; inspect per-file backup coverage.',
    'INSTALL_PARTIAL': 'Installation is incomplete. New files are retained; select the same fixed package to repair. Do not treat it as activated or ready to restart.',
    'LEGACY_MODULE_PROOF_INVALID': 'The exact prior ECM300 module evidence is missing or changed. Preserve the current module and original transaction records for review.',
    'MODULE_RENAME_CONFLICT': 'Review the named unmanaged module or old loader reference. No file was guessed or deleted; preserve it and resolve its owner before retrying.',
    'MODULE_RENAME_CHECK_FAILED': 'Could not establish the current module/loader inventory for the JP6 name transition. Review the saved cause; no migration was attempted.',
    'OPERATION_INTERRUPTED': 'The owned executor was interrupted. Inspect its recovery receipt and active marker before another explicit operation.',
    'SESSION_BUSY': 'Close the other Manager normally and retry explicitly. Do not delete lock files or kill unrelated work.',
    'OK': 'Restart the Jetson manually.',
    'USER_CANCELLED': 'No installation was performed. Review the package when ready.',
    'AWAITING_ARTIFACTS': 'Obtain the complete fixed package before installation.',
    'ENVIRONMENT_WARNING': 'Review recorded environment differences; normal confirmation remains available.',
    'LOG_UNAVAILABLE': 'Use the emergency record if available; restore log storage access before retrying.',
    'LOG_WRITE_FAILED': 'Preserve this record and check log storage; inspect transaction/recovery status before retrying.',
    'ACCESS_DENIED': 'Check access to the recorded path; do not assume installation completed.',
    'REQUIRED_FILE_MISSING': 'Restore the required file from the fixed package or recorded system baseline.',
    'ARTIFACT_MISSING': 'Obtain the complete fixed package; do not substitute another artifact.',
    'ARTIFACT_INTEGRITY': 'Replace the damaged package with the fixed, hash-matched artifact set.',
    'MANIFEST_INTEGRITY': 'Use the manifest bound to the selected release index.',
    'PACKAGE_INVALID': 'Check the package identity, manifest and evidence bundle.',
    'PROVENANCE_INVALID': 'Obtain the complete matching source/build evidence bundle.',
    'NATIVE_WRITES_DISABLED': 'This candidate permits local review only; native mutation remains disabled.',
    'MODULE_PATH_MISMATCH': 'Inspect the recorded expected/actual module paths and dependency index.',
    'NATIVE_REQUEST_INVALID': 'Preserve the report; select the fixed package again after reviewing identity differences.',
    'NATIVE_PLATFORM_UNSUPPORTED': 'Native execution requires Linux; this host permits local review only.',
    'AUTH_OR_EXECUTOR_FAILED': 'Authentication or executor startup did not complete; inspect this report before a new explicit attempt.',
    'EXECUTOR_INCOMPLETE': 'Inspect the recorded transaction and active marker; do not automatically retry installation.',
    'WORKSPACE_CHANGED': 'Use the original explicit workspace; review any changes before proceeding.',
    'MULTIPLE_WORKSPACES': 'Choose one existing workspace explicitly with --workspace; histories are not merged.',
    'LEGACY_UNSUPPORTED': 'Use the original owning recovery workflow; typed legacy state remains preserved.',
    'TRANSACTION_INCOMPLETE': 'Preserve the workspace and inspect the required transaction chain before proceeding.',
    'UNOWNED_OVERLAY': 'The active overlay has no matching managed chain; preserve it and review its owner.',
    'CAMERA_BUSY': 'Close the identified camera client normally and confirm again; no process was killed.',
    'NATIVE_DESTINATION_INVALID': 'Review the destination against the fixed package and recorded managed chain.',
}
for stage, (_, code) in STAGES.items():
    CODES.setdefault(code, 'Inspect the recorded stage, command/path and original cause; review recovery before retrying.')


class DiagnosticError(ValueError):
    def __init__(self, message, code, *, stage=None, **context):
        super().__init__(message)
        if code not in CODES: raise ValueError('Unknown diagnostic code')
        self.error_code, self.stage, self.diagnostic_context = code, stage, context


class NativeWritesDisabled(RuntimeError):
    error_code = 'NATIVE_WRITES_DISABLED'


class OperationFailure(RuntimeError):
    def __init__(self, primary, transaction_id, recovery_errors, diagnostic_errors=()):
        self.details = dict(primary, transaction_id=transaction_id, recovery_errors=list(recovery_errors),
                            diagnostic_errors=list(diagnostic_errors),
                            recovery_status='RECOVERY_REQUIRED' if recovery_errors else 'FILES_RESTORED' if transaction_id else 'NOT_STARTED')
        super().__init__('Operation failed: ' + primary['message'] + '; rollback=' +
                         ('recovery required: ' + repr(recovery_errors) if recovery_errors else 'verified') +
                         '; transaction=' + str(transaction_id))

class RecordedFailure(RuntimeError):
    """Forward a child diagnostic without inferring recovery from its exit code."""
    def __init__(self, detail):
        self.details=dict(detail)
        super().__init__(detail['message'])


def error_detail(exc, stage, **context):
    if isinstance(exc, (OperationFailure,RecordedFailure)): return dict(exc.details)
    code = getattr(exc, 'error_code', None)
    if code is None:
        code = 'ACCESS_DENIED' if isinstance(exc, PermissionError) else 'REQUIRED_FILE_MISSING' if isinstance(exc, FileNotFoundError) else STAGES.get(stage, STAGES['result'])[1]
    chain=[]; cause=exc
    for _ in range(8):
        chain.append({'type':type(cause).__name__, 'message':str(cause)})
        cause=cause.__cause__
        if cause is None: break
    return dict(stage=getattr(exc,'stage',None) or stage, error_code=code, message=str(exc),
                exception_type=type(exc).__name__, exception_chain=chain,
                traceback=''.join(traceback.format_exception(type(exc),exc,exc.__traceback__)),
                context=dict(getattr(exc,'diagnostic_context',{}),**context), next_action=CODES[code])

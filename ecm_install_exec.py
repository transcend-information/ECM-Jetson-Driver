"""Bounded privileged installer child; never opens a menu."""
import argparse,sys,signal
from pathlib import Path
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
from ecm_env.check import require_system_python
require_system_python()
from ecm_install.privileged import execute_request

def main():
    parser=argparse.ArgumentParser(description='Confirmed fixed installation executor')
    parser.add_argument('--request',required=True);parser.add_argument('--sha256',required=True)
    args=parser.parse_args()
    from ecm_install.diagnostics import DiagnosticError
    def interrupted(signum,frame):
        # Exception (not BaseException) enters the existing journalled rollback.
        raise DiagnosticError('Installation interrupted by signal '+str(signum),'OPERATION_INTERRUPTED')
    signal.signal(signal.SIGINT,interrupted);signal.signal(signal.SIGTERM,interrupted)
    try:return execute_request(args.request,args.sha256)
    except Exception as exc:
        print('Installation executor could not complete: '+str(exc),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())

"""R20 terminal rendering primitives; installation screens only."""
import os
import shutil
import sys
import textwrap

def interactive():
    """Only a real input/output terminal can have its viewport redrawn."""
    return (bool(getattr(sys.stdin, 'isatty', lambda: False)()) and
            bool(getattr(sys.stdout, 'isatty', lambda: False)()) and
            os.environ.get('TERM', '').lower() != 'dumb')


def width():
    return max(40, min(100, shutil.get_terminal_size((96, 24)).columns))


def line(text=''):
    for part in str(text).split('\n'):
        # Keep paths and identifiers copyable; a terminal may wrap a long token.
        wrapped = textwrap.wrap(part, width=width(), break_long_words=False,
                                break_on_hyphens=False, replace_whitespace=False)
        print('\n'.join(wrapped) if wrapped else '')


def rule():
    print('-' * width())


def begin(breadcrumb, version, context=()):
    if interactive():
        # Clear the viewport, not scrollback (3J) or any persistent log.
        print('\x1b[2J\x1b[H', end='', flush=True)
    print('=' * width())
    line('ECM Camera Manager | ' + str(version))
    line(str(breadcrumb))
    rule()
    for item in context:
        line(item)
    if context:
        rule()


def footer(*instructions):
    rule()
    for item in instructions:
        line(item)

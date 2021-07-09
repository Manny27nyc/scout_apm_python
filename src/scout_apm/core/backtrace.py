# coding=utf-8
from __future__ import absolute_import, division, print_function, unicode_literals

import itertools
import sys
import sysconfig
import traceback
import warnings

# Maximum non-Scout frames to target retrieving
LIMIT = 50
# How many upper frames from inside Scout to ignore
IGNORED = 1


def filter_frames(frames):
    """Filter the stack trace frames down to non-library code."""
    paths = sysconfig.get_paths()
    library_paths = {paths["purelib"], paths["platlib"]}
    for frame in frames:
        if not any(frame["file"].startswith(exclusion) for exclusion in library_paths):
            yield frame


if sys.version_info >= (3, 5):

    def stacktrace_walker(tb):
        """Iterate over each frame of the stack downards for exceptions."""
        for frame, lineno in traceback.walk_tb(tb):
            co = frame.f_code
            filename = co.co_filename
            name = co.co_name
            yield {"file": filename, "line": lineno, "function": name}

    def backtrace_walker():
        """Iterate over each frame of the stack upwards.

        Taken from python3/traceback.ExtractSummary.extract to support
        iterating over the entire stack, but without creating a large
        data structure.
        """
        start_frame = sys._getframe().f_back
        for frame, lineno in traceback.walk_stack(start_frame):
            co = frame.f_code
            filename = co.co_filename
            name = co.co_name
            yield {"file": filename, "line": lineno, "function": name}


else:

    def stacktrace_walker(tb):
        """Iterate over each frame of the stack downards for exceptions."""
        while tb is not None:
            lineno = tb.tb_lineno
            co = tb.tb_frame.f_code
            filename = co.co_filename
            name = co.co_name
            yield {"file": filename, "line": lineno, "function": name}
            tb = tb.tb_next

    def backtrace_walker():
        """Iterate over each frame of the stack upwards.

        Taken from python2.7/traceback.extract_stack to support iterating
        over the entire stack, but without creating a large data structure.
        """
        try:
            raise ZeroDivisionError
        except ZeroDivisionError:
            # Get the current frame
            f = sys.exc_info()[2].tb_frame.f_back

        while f is not None:
            lineno = f.f_lineno
            co = f.f_code
            filename = co.co_filename
            name = co.co_name
            yield {"file": filename, "line": lineno, "function": name}
            f = f.f_back


def capture_backtrace():
    walker = filter_frames(backtrace_walker())
    return list(itertools.islice(walker, LIMIT))


def capture_stacktrace(tb):
    walker = stacktrace_walker(tb)
    return list(reversed(list(itertools.islice(walker, LIMIT))))


def capture():
    warnings.warn(
        "capture is deprecated, instead use capture_backtrace instead.",
        DeprecationWarning,
        2,
    )
    return capture_backtrace()

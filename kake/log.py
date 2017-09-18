"""Logging utilities, including the kake-specific logger.

We add new 'verbose' logging levels: instead of DEBUG, you can use V1,
V2, V3, and V4.  The higher the number, the more output you get.

log.info(), log.v1(), etc. all use the kake logger rather than the
global logger (which you would get with logging.info(), logging.v1(),
etc.)

We also provide a convenience routine to ada a --verbose flag to an
app, that will automatically set the kake-logger log-level.  This is
for use in kake apps; since it doesn't affect the global logger, it's
not appropriate elsewhere.
"""

from __future__ import absolute_import

import argparse
import logging
import time


# We choose the levels to be between INFO and DEBUG.
logging.V1 = 19
logging.V2 = logging.V1 - 1
logging.V3 = logging.V2 - 1
logging.V4 = logging.V3 - 1
logging.addLevelName(logging.V1, 'V1')
logging.addLevelName(logging.V2, 'V2')
logging.addLevelName(logging.V3, 'V3')
logging.addLevelName(logging.V4, 'V4')
logging.Logger.v1 = lambda self, *a, **kw: self.log(logging.V1, *a, **kw)
logging.Logger.v2 = lambda self, *a, **kw: self.log(logging.V2, *a, **kw)
logging.Logger.v3 = lambda self, *a, **kw: self.log(logging.V3, *a, **kw)
logging.Logger.v4 = lambda self, *a, **kw: self.log(logging.V4, *a, **kw)


_KAKE_LOGGER = logging.getLogger('kake')
if not _KAKE_LOGGER.handlers:
    # Set the logger up to format like this:
    #    [131106 12:54:03.640 V1] hello, world
    _KAKE_FORMATTER = logging.Formatter(
        '[%(asctime)s.%(msecs).03d %(levelname)s] %(message)s',
        datefmt='%y%m%d %H:%M:%S')
    # logging.py has a bug(?) where Formatter.converter gets sad if
    # time.localtime is not a built-in function.  (It has to do with
    # the fact that built-ins functions are never converted to bound
    # methods, while regular functions are.)  Unfortunately, converter
    # is not a built-in when we're using faketime.  We can fix this by
    # reassigning the converter to the formatter *instance* (rather
    # than class).
    _KAKE_FORMATTER.converter = time.localtime
    _KAKE_HANDLER = logging.StreamHandler()
    _KAKE_HANDLER.setFormatter(_KAKE_FORMATTER)
    _KAKE_LOGGER.addHandler(_KAKE_HANDLER)
    _KAKE_LOGGER.setLevel(logging.INFO)    # the default log level for us
    _KAKE_LOGGER.propagate = 0             # don't have the root logger log too


def logger():
    """Return a logger object that supports info(), v1(), v2(), etc."""
    return _KAKE_LOGGER


def set_log_level(level):
    """Set the log level for the kake logger: logging.INFO, logging.V1, etc."""
    _KAKE_LOGGER.setLevel(level)


# Convenience functions that call the various logging routines on the
# kake logger.
fatal = _KAKE_LOGGER.fatal
exception = _KAKE_LOGGER.exception
critical = _KAKE_LOGGER.critical
error = _KAKE_LOGGER.error
warning = _KAKE_LOGGER.warning
info = _KAKE_LOGGER.info
v1 = _KAKE_LOGGER.v1
v2 = _KAKE_LOGGER.v2
v3 = _KAKE_LOGGER.v3
v4 = _KAKE_LOGGER.v4
debug = _KAKE_LOGGER.debug


def add_verbose_flag(arg_parser, default='info'):
    """Add a flag to an argparse parser to automatically set the log level."""
    class VerboseAction(argparse.Action):
        """Set the log level right when the --verbose flag is parsed."""
        def __call__(self, parser, namespace, values, option_string=None):
            value = values.upper()         # info -> INFO
            if hasattr(logging, value):    # logging.XX exists, e.g. 'INFO'
                set_log_level(getattr(logging, value))
            elif hasattr(logging, 'V%s' % value):     # V1, V2, etc.
                set_log_level(getattr(logging, 'V%s' % value))
            else:
                raise ValueError('Unknown value for --verbose: %s', values)

    arg_parser.add_argument(
        '--verbose', '-v',
        choices=['1', '2', '3', '4', 'info', 'warning', 'error'],
        action=VerboseAction,
        default=default,
        help=('Verbose output; the higher the number, the more the output'
              ' (default: %(default)s)'))

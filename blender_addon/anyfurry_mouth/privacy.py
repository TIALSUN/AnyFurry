"""Keep local filenames and profile directories out of shareable error messages."""
import re

_PROFILE_PATH = re.compile(r"(?i)[a-z]:[\\/]+(?:users|documents and settings)[\\/]+[^'\"\r\n]+|/home/[^'\"\r\n]+")


def error_message(error):
    message = str(error)
    # OSError filenames can identify a person even outside a profile directory.
    for attribute in ('filename', 'filename2'):
        filename = getattr(error, attribute, None)
        if filename:
            message = message.replace(repr(str(filename)), repr('[本地文件]'))
            message = message.replace(str(filename), '[本地文件]')
    return _PROFILE_PATH.sub('[本地路径]', message)

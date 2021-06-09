import errno
import logging
import os
import subprocess
import collections
from tempfile import NamedTemporaryFile

from bumpversion.exceptions import (
    WorkingDirectoryIsDirtyException,
    MercurialDoesNotSupportSignedTagsException,
)


logger = logging.getLogger(__name__)


class BaseVCS:

    _TEST_USABLE_COMMAND = None
    _COMMIT_COMMAND = None

    @classmethod
    def commit(cls, message, context, extra_args=None):
        extra_args = extra_args or []
        with NamedTemporaryFile("wb", delete=False) as f:
            f.write(message.encode("utf-8"))
        env = os.environ.copy()
        env["HGENCODING"] = "utf-8"
        for key in ("current_version", "new_version"):
            env[str("BUMPVERSION_" + key.upper())] = str(context[key])
        try:
            subprocess.check_output(
                cls._COMMIT_COMMAND + [f.name] + extra_args, env=env
            )
        except subprocess.CalledProcessError as exc:
            err_msg = "Failed to run {}: return code {}, output: {}".format(
                exc.cmd, exc.returncode, exc.output
            )
            logger.exception(err_msg)
            raise exc
        finally:
            os.unlink(f.name)

    @classmethod
    def is_usable(cls):
        try:
            return (
                subprocess.call(
                    cls._TEST_USABLE_COMMAND,
                    stderr=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                )
                == 0
            )
        except OSError as e:
            if e.errno in (errno.ENOENT, errno.EACCES, errno.ENOTDIR):
                return False
            raise


class Git(BaseVCS):

    _TEST_USABLE_COMMAND = ["git", "rev-parse", "--git-dir"]
    _COMMIT_COMMAND = ["git", "commit", "-F"]

    @classmethod
    def assert_nondirty(cls):
        lines = [
            line.strip()
            for line in subprocess.check_output(
                ["git", "status", "--porcelain"]
            ).splitlines()
            if not line.strip().startswith(b"??")
        ]

        if lines:
            raise WorkingDirectoryIsDirtyException(
                "Git working directory is not clean:\n{}".format(
                    b"\n".join(lines).decode()
                )
            )

    @classmethod
    def latest_tag_info(cls):
        try:
            # git-describe doesn't update the git-index, so we do that
            subprocess.check_output(["git", "update-index", "--refresh"])

            # get info about the latest tag in git
            describe_out = (
                subprocess.check_output(
                    [
                        "git",
                        "describe",
                        "--dirty",
                        "--tags",
                        "--long",
                        "--abbrev=40",
                        "--match=v*",
                    ],
                    stderr=subprocess.STDOUT,
                )
                .decode()
                .split("-")
            )
        except subprocess.CalledProcessError:
            logger.debug("Error when running git describe")
            return {}

        info = {}

        if describe_out[-1].strip() == "dirty":
            info["dirty"] = True
            describe_out.pop()

        info["commit_sha"] = describe_out.pop().lstrip("g")
        info["distance_to_latest_tag"] = int(describe_out.pop())
        info["current_version"] = "-".join(describe_out).lstrip("v")

        return info

    @classmethod
    def add_path(cls, path):
        subprocess.check_output(["git", "add", "--update", path])

    @classmethod
    def tag(cls, sign, name, message):
        """
        Create a tag of the new_version in VCS.

        If only name is given, bumpversion uses a lightweight tag.
        Otherwise, it utilizes an annotated tag.
        """
        command = ["git", "tag", name]
        if sign:
            command += ["--sign"]
        if message:
            command += ["--message", message]
        subprocess.check_output(command)


class Mercurial(BaseVCS):

    _TEST_USABLE_COMMAND = ["hg", "root"]
    _COMMIT_COMMAND = ["hg", "commit", "--logfile"]

    @classmethod
    def latest_tag_info(cls):
        return {}

    @classmethod
    def assert_nondirty(cls):
        lines = [
            line.strip()
            for line in subprocess.check_output(["hg", "status", "-mard"]).splitlines()
            if not line.strip().startswith(b"??")
        ]

        if lines:
            raise WorkingDirectoryIsDirtyException(
                "Mercurial working directory is not clean:\n{}".format(
                    b"\n".join(lines).decode()
                )
             )

    @classmethod
    def add_path(cls, path):
        pass

    @classmethod
    def tag(cls, sign, name, message):
        command = ["hg", "tag", name]
        if sign:
            raise MercurialDoesNotSupportSignedTagsException(
                "Mercurial does not support signed tags."
            )
        if message:
            command += ["--message", message]
        subprocess.check_output(command)


RepoUrls = collections.namedtuple('RepoUrls', ['base', 'root', 'current'])

class SVN(BaseVCS):

    _COMMIT_COMMAND = ["svn", "commit", "--file"]

    @classmethod
    def current_url(cls):
        output = subprocess.check_output(["svn", "info"]).decode().split('\n')
        for line in output:
            if line.startswith('URL:'):
                return line.lstrip('URL: ')

        else:
            return None        

    @classmethod
    def repo_urls(cls):
        curr_url = cls.current_url()
        base = root = None
        if curr_url is not None:
            for folder in ["branch", "branches", "trunk"]:
                try:
                    idx = curr_url.rindex(folder)
                except ValueError:
                    continue
                else:
                    base = curr_url[:idx - 1]
                    root = curr_url[:idx + len(folder)]
                    break
        return RepoUrls(base, root, curr_url)

    @classmethod
    def is_usable(cls):
        return bool(cls.repo_urls().base)

    @classmethod
    def latest_tag_info(cls):
        return {}

    @classmethod
    def assert_nondirty(cls):
        uncommited = subprocess.check_output(["svn", "status", "-q"])
        if uncommited:
            raise WorkingDirectoryIsDirtyException(
                "SVN working directory is not clean:\n{}".format(
                    uncommited))

    @classmethod
    def add_path(cls, path):
        _COMMIT_COMMAND = _COMMIT_COMMAND[:-1] + [path] + _COMMIT_COMMAND[-1]
    
    @classmethod
    def tag(cls, sign, name, message):
        """
        Create a tag of the new_version in svn.
        """
        urls = cls.repo_urls()
        command = ["svn", "copy", urls.root,
                   urls.base + "/tags/" + name, "--message"]
        command.append('"{}"'.format(message) if message else '""')
        subprocess.check_output(command)

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Final, override

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

GOVERNING_RULE: Final = "docs/architecture.md#capability-effect-boundaries"
PACKAGE_NAME: Final = "agentic_investment_os"
EFFECT_ZONES: Final = frozenset({"adapters", "entrypoints"})
type _Prohibition = tuple[str, str, str]

_FORBIDDEN_IMPORTS: Final = (
    ("aiohttp", "CAP005", "concrete network dependency"),
    ("ftplib", "CAP005", "concrete network dependency"),
    ("http.client", "CAP005", "concrete network dependency"),
    ("http.server", "CAP005", "concrete network dependency"),
    ("httpx", "CAP005", "concrete network dependency"),
    ("imaplib", "CAP005", "concrete network dependency"),
    ("nntplib", "CAP005", "concrete network dependency"),
    ("poplib", "CAP005", "concrete network dependency"),
    ("requests", "CAP005", "concrete network dependency"),
    ("smtplib", "CAP005", "concrete network dependency"),
    ("socket", "CAP005", "concrete network dependency"),
    ("socketserver", "CAP005", "concrete network dependency"),
    ("urllib.request", "CAP005", "concrete network dependency"),
    ("urllib3", "CAP005", "concrete network dependency"),
    ("websockets", "CAP005", "concrete network dependency"),
    ("anthropic", "CAP006", "concrete model-client dependency"),
    ("cohere", "CAP006", "concrete model-client dependency"),
    ("google.generativeai", "CAP006", "concrete model-client dependency"),
    ("google.genai", "CAP006", "concrete model-client dependency"),
    ("mistralai", "CAP006", "concrete model-client dependency"),
    ("ollama", "CAP006", "concrete model-client dependency"),
    ("openai", "CAP006", "concrete model-client dependency"),
    ("alpaca", "CAP007", "concrete broker-client dependency"),
    ("alpaca_trade_api", "CAP007", "concrete broker-client dependency"),
    ("apsw", "CAP008", "concrete SQLite dependency"),
    ("sqlite3", "CAP008", "concrete SQLite dependency"),
    ("multiprocessing", "CAP010", "external-process dependency"),
    ("subprocess", "CAP010", "external-process dependency"),
)

_AMBIENT_TIME_CALLS: Final = frozenset(
    {
        "asyncio.AbstractEventLoop.time",
        "datetime.date.today",
        "datetime.datetime.now",
        "datetime.datetime.today",
        "datetime.datetime.utcnow",
        "time.clock_gettime",
        "time.clock_gettime_ns",
        "time.monotonic",
        "time.monotonic_ns",
        "time.perf_counter",
        "time.perf_counter_ns",
        "time.process_time",
        "time.process_time_ns",
        "time.thread_time",
        "time.thread_time_ns",
        "time.time",
        "time.time_ns",
    }
)
_OPTIONAL_AMBIENT_TIME_CALLS: Final = {
    "time.asctime": (0, frozenset({"t"})),
    "time.gmtime": (0, frozenset({"secs", "seconds"})),
    "time.strftime": (1, frozenset({"t"})),
}
_LOCAL_TIME_CALLS: Final = frozenset(
    {
        "datetime.date.fromtimestamp",
        "time.ctime",
        "time.localtime",
        "time.mktime",
    }
)
_OPTIONAL_LOCAL_TIME_CALLS: Final = {
    "datetime.datetime.astimezone": (0, frozenset({"tz"})),
    "datetime.datetime.fromtimestamp": (1, frozenset({"tz"})),
}
_DATETIME_RETURNING_CALLS: Final = frozenset(
    {
        "datetime.datetime",
        "datetime.datetime.combine",
        "datetime.datetime.fromisocalendar",
        "datetime.datetime.fromisoformat",
        "datetime.datetime.fromordinal",
        "datetime.datetime.fromtimestamp",
        "datetime.datetime.strptime",
        "datetime.datetime.utcfromtimestamp",
    }
)
_AMBIENT_UUID_CALLS: Final = frozenset(
    {"uuid.getnode", "uuid.uuid1", "uuid.uuid4", "uuid.uuid6", "uuid.uuid7", "uuid.uuid8"}
)
_RANDOM_CALLS: Final = frozenset(
    {
        "os.getrandom",
        "os.urandom",
        "random.betavariate",
        "random.binomialvariate",
        "random.choice",
        "random.choices",
        "random.expovariate",
        "random.gammavariate",
        "random.gauss",
        "random.getrandbits",
        "random.lognormvariate",
        "random.normalvariate",
        "random.paretovariate",
        "random.randbytes",
        "random.randint",
        "random.random",
        "random.randrange",
        "random.sample",
        "random.seed",
        "random.shuffle",
        "random.getstate",
        "random.setstate",
        "random.triangular",
        "random.uniform",
        "random.vonmisesvariate",
        "random.weibullvariate",
        "secrets.choice",
        "secrets.randbelow",
        "secrets.randbits",
        "secrets.token_bytes",
        "secrets.token_hex",
        "secrets.token_urlsafe",
    }
)
_SYSTEM_RANDOM_CALLS: Final = frozenset({"random.SystemRandom", "secrets.SystemRandom"})
_ENVIRONMENT_CALLS: Final = frozenset(
    {
        "getpass.getuser",
        "os.get_exec_path",
        "os.getenv",
        "os.getenvb",
        "os.path.expanduser",
        "os.path.expandvars",
        "os.putenv",
        "os.unsetenv",
        "time.tzset",
    }
)
_ENVIRONMENT_OBJECTS: Final = frozenset({"os.environ", "os.environb"})
_AMBIENT_ENVIRONMENT_VALUES: Final = frozenset(
    {"time.altzone", "time.daylight", "time.timezone", "time.tzname"}
)
_NETWORK_CALLS: Final = frozenset(
    {
        "anyio.connect_tcp",
        "anyio.connect_unix",
        "asyncio.open_connection",
        "asyncio.open_unix_connection",
        "asyncio.start_server",
        "asyncio.start_unix_server",
        "asyncio.streams.open_connection",
        "asyncio.streams.open_unix_connection",
        "asyncio.streams.start_server",
        "asyncio.streams.start_unix_server",
        "logging.config.listen",
        "logging.handlers.DatagramHandler",
        "logging.handlers.HTTPHandler",
        "logging.handlers.SMTPHandler",
        "logging.handlers.SocketHandler",
        "logging.handlers.SysLogHandler",
        "trio.open_tcp_listeners",
        "trio.open_tcp_stream",
        "trio.open_unix_socket",
        "trio.serve_listeners",
        "trio.serve_tcp",
    }
)
_NETWORK_LOOP_METHODS: Final = frozenset(
    {
        "connect_accepted_socket",
        "create_connection",
        "create_datagram_endpoint",
        "create_server",
        "create_unix_connection",
        "create_unix_server",
        "getaddrinfo",
        "getnameinfo",
        "sendfile",
        "sock_accept",
        "sock_connect",
        "sock_recv",
        "sock_recv_into",
        "sock_recvfrom",
        "sock_recvfrom_into",
        "sock_sendall",
        "sock_sendfile",
        "sock_sendto",
        "start_tls",
    }
)
_EVENT_LOOP_RETURNING_CALLS: Final = frozenset(
    {
        "asyncio.AbstractEventLoopPolicy.get_event_loop",
        "asyncio.AbstractEventLoopPolicy.new_event_loop",
        "asyncio.ProactorEventLoop",
        "asyncio.Runner.get_loop",
        "asyncio.SelectorEventLoop",
        "asyncio.events.get_event_loop",
        "asyncio.events.get_running_loop",
        "asyncio.events.new_event_loop",
        "asyncio.get_event_loop",
        "asyncio.get_running_loop",
        "asyncio.new_event_loop",
    }
)
_EVENT_LOOP_POLICY_RETURNING_CALLS: Final = frozenset(
    {"asyncio.events.get_event_loop_policy", "asyncio.get_event_loop_policy"}
)
_FILESYSTEM_CALLS: Final = frozenset(
    {
        "aifc.open",
        "aifc.Aifc_read",
        "aifc.Aifc_write",
        "bz2.BZ2File",
        "bz2.open",
        "builtins.open",
        "codecs.open",
        "configparser.ConfigParser.read",
        "configparser.RawConfigParser.read",
        "dbm.dumb.open",
        "dbm.gnu.open",
        "dbm.ndbm.open",
        "dbm.open",
        "dbm.whichdb",
        "filecmp.cmp",
        "filecmp.cmpfiles",
        "filecmp.dircmp",
        "fileinput.FileInput",
        "fileinput.input",
        "glob.glob",
        "glob.iglob",
        "gzip.open",
        "gzip.GzipFile",
        "importlib.resources.as_file",
        "importlib.resources.contents",
        "importlib.resources.files",
        "importlib.resources.is_resource",
        "importlib.resources.open_binary",
        "importlib.resources.open_text",
        "importlib.resources.path",
        "importlib.resources.read_binary",
        "importlib.resources.read_text",
        "io.open",
        "io.FileIO",
        "io.open_code",
        "linecache.checkcache",
        "linecache.getline",
        "linecache.getlines",
        "logging.FileHandler",
        "logging.handlers.RotatingFileHandler",
        "logging.handlers.TimedRotatingFileHandler",
        "logging.handlers.WatchedFileHandler",
        "logging.config.fileConfig",
        "lzma.LZMAFile",
        "lzma.open",
        "mailbox.Babyl",
        "mailbox.Maildir",
        "mailbox.MH",
        "mailbox.MMDF",
        "mailbox.mbox",
        "mmap.mmap",
        "open",
        "os.access",
        "os.chdir",
        "os.chflags",
        "os.chmod",
        "os.chown",
        "os.chroot",
        "os.close",
        "os.closerange",
        "os.copy_file_range",
        "os.dup",
        "os.dup2",
        "os.fdatasync",
        "os.fdopen",
        "os.fchdir",
        "os.fchmod",
        "os.fchown",
        "os.fpathconf",
        "os.fstat",
        "os.fstatvfs",
        "os.fsync",
        "os.ftruncate",
        "os.fwalk",
        "os.getcwd",
        "os.getcwdb",
        "os.getxattr",
        "os.lchflags",
        "os.lchmod",
        "os.lchown",
        "os.link",
        "os.listdir",
        "os.listdrives",
        "os.listmounts",
        "os.listvolumes",
        "os.listxattr",
        "os.lockf",
        "os.lseek",
        "os.lstat",
        "os.makedirs",
        "os.mkdir",
        "os.mkfifo",
        "os.mknod",
        "os.open",
        "os.openpty",
        "os.path.abspath",
        "os.path.exists",
        "os.path.getatime",
        "os.path.getctime",
        "os.path.getmtime",
        "os.path.getsize",
        "os.path.isdir",
        "os.path.isfile",
        "os.path.isdevdrive",
        "os.path.isjunction",
        "os.path.islink",
        "os.path.ismount",
        "os.path.lexists",
        "os.path.realpath",
        "os.path.relpath",
        "os.path.samefile",
        "os.path.sameopenfile",
        "os.path.samestat",
        "os.pathconf",
        "os.pipe",
        "os.pipe2",
        "os.posix_fadvise",
        "os.posix_fallocate",
        "os.pread",
        "os.preadv",
        "os.pwrite",
        "os.pwritev",
        "os.read",
        "os.readlink",
        "os.readv",
        "os.remove",
        "os.removedirs",
        "os.removexattr",
        "os.rename",
        "os.renames",
        "os.replace",
        "os.rmdir",
        "os.scandir",
        "os.sendfile",
        "os.setxattr",
        "os.splice",
        "os.stat",
        "os.statvfs",
        "os.sync",
        "os.symlink",
        "os.truncate",
        "os.ttyname",
        "os.umask",
        "os.unlink",
        "os.utime",
        "os.walk",
        "os.write",
        "os.writev",
        "pkgutil.extend_path",
        "pkgutil.find_loader",
        "pkgutil.get_data",
        "pkgutil.get_importer",
        "pkgutil.get_loader",
        "pkgutil.iter_importers",
        "pkgutil.iter_modules",
        "pkgutil.walk_packages",
        "runpy.run_path",
        "shutil.chown",
        "shutil.copy",
        "shutil.copy2",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.copytree",
        "shutil.disk_usage",
        "shutil.make_archive",
        "shutil.move",
        "shutil.rmtree",
        "shutil.unpack_archive",
        "shutil.which",
        "shelve.DbfilenameShelf",
        "shelve.open",
        "sndhdr.what",
        "sunau.open",
        "sunau.Au_read",
        "sunau.Au_write",
        "tarfile.is_tarfile",
        "tarfile.TarFile",
        "tarfile.open",
        "tempfile.NamedTemporaryFile",
        "tempfile.SpooledTemporaryFile",
        "tempfile.TemporaryDirectory",
        "tempfile.TemporaryFile",
        "tempfile.gettempdir",
        "tempfile.gettempdirb",
        "tempfile.mkdtemp",
        "tempfile.mkstemp",
        "tempfile.mktemp",
        "tokenize.open",
        "wave.open",
        "wave.Wave_read",
        "wave.Wave_write",
        "xml.dom.minidom.parse",
        "xml.etree.ElementTree.parse",
        "xml.etree.ElementTree.iterparse",
        "xml.sax.parse",
        "zipfile.PyZipFile",
        "zipfile.Path",
        "zipfile.is_zipfile",
        "zipfile.ZipFile",
        "zipimport.zipimporter",
        "importlib.machinery.PathFinder.find_spec",
    }
)
_OPTIONAL_FILESYSTEM_CALLS: Final = {
    "logging.basicConfig": frozenset({"filename"}),
}
_PATH_TYPES: Final = frozenset({"pathlib.Path", "pathlib.PosixPath", "pathlib.WindowsPath"})
_CONFIG_PARSER_TYPES: Final = frozenset(
    {"configparser.ConfigParser", "configparser.RawConfigParser"}
)
_NETWORK_EFFECT_RECEIVER_TYPES: Final = frozenset(
    {
        "asyncio.AbstractServer",
        "asyncio.BaseTransport",
        "asyncio.DatagramTransport",
        "asyncio.ReadTransport",
        "asyncio.Server",
        "asyncio.StreamReader",
        "asyncio.StreamWriter",
        "asyncio.Transport",
        "asyncio.WriteTransport",
        "asyncio.base_events.Server",
        "asyncio.events.AbstractServer",
        "asyncio.streams.StreamReader",
        "asyncio.streams.StreamWriter",
        "asyncio.transports.BaseTransport",
        "asyncio.transports.DatagramTransport",
        "asyncio.transports.ReadTransport",
        "asyncio.transports.Transport",
        "asyncio.transports.WriteTransport",
        "logging.handlers.DatagramHandler",
        "logging.handlers.HTTPHandler",
        "logging.handlers.SMTPHandler",
        "logging.handlers.SocketHandler",
        "logging.handlers.SysLogHandler",
    }
)
_FILESYSTEM_EFFECT_RECEIVER_TYPES: Final = frozenset(
    {
        "bz2.BZ2File",
        "aifc.Aifc_read",
        "aifc.Aifc_write",
        "filecmp.dircmp",
        "fileinput.FileInput",
        "gzip.GzipFile",
        "importlib.abc.ResourceReader",
        "importlib.machinery.ExtensionFileLoader",
        "importlib.machinery.FileFinder",
        "importlib.machinery.PathFinder",
        "importlib.machinery.SourceFileLoader",
        "importlib.machinery.SourcelessFileLoader",
        "logging.FileHandler",
        "logging.handlers.RotatingFileHandler",
        "logging.handlers.TimedRotatingFileHandler",
        "logging.handlers.WatchedFileHandler",
        "lzma.LZMAFile",
        "mailbox.Babyl",
        "mailbox.Maildir",
        "mailbox.MH",
        "mailbox.MMDF",
        "mailbox.mbox",
        "mmap.mmap",
        "io.FileIO",
        "os.DirEntry",
        "shelve.DbfilenameShelf",
        "shelve.Shelf",
        "tarfile.TarFile",
        "tarfile.ExFileObject",
        "tempfile.SpooledTemporaryFile",
        "tempfile.TemporaryDirectory",
        "sunau.Au_read",
        "sunau.Au_write",
        "wave.Wave_read",
        "wave.Wave_write",
        "zipfile.PyZipFile",
        "zipfile.Path",
        "zipfile.ZipExtFile",
        "zipfile.ZipFile",
        "zipimport.zipimporter",
        "importlib.resources.abc.ResourceReader",
        "importlib.resources.abc.Traversable",
        "importlib.resources.abc.TraversableResources",
    }
)
_PROCESS_EFFECT_RECEIVER_TYPES: Final = frozenset(
    {
        "asyncio.SubprocessTransport",
        "asyncio.base_subprocess.BaseSubprocessTransport",
        "asyncio.subprocess.Process",
        "asyncio.transports.SubprocessTransport",
        "concurrent.futures.ProcessPoolExecutor",
        "concurrent.futures.process.ProcessPoolExecutor",
    }
)
_EFFECT_RECEIVER_TYPES: Final = (
    _FILESYSTEM_EFFECT_RECEIVER_TYPES
    | _NETWORK_EFFECT_RECEIVER_TYPES
    | _PROCESS_EFFECT_RECEIVER_TYPES
)
_PATH_EFFECT_METHODS: Final = frozenset(
    {
        "absolute",
        "chmod",
        "cwd",
        "exists",
        "expanduser",
        "glob",
        "group",
        "hardlink_to",
        "home",
        "is_block_device",
        "is_char_device",
        "is_dir",
        "is_fifo",
        "is_file",
        "is_junction",
        "is_mount",
        "is_socket",
        "is_symlink",
        "iterdir",
        "lchmod",
        "link_to",
        "lstat",
        "mkdir",
        "open",
        "owner",
        "read_bytes",
        "read_text",
        "readlink",
        "rename",
        "replace",
        "resolve",
        "rglob",
        "rmdir",
        "samefile",
        "stat",
        "symlink_to",
        "touch",
        "unlink",
        "walk",
        "write_bytes",
        "write_text",
    }
)
_PATH_RETURNING_METHODS: Final = frozenset(
    {
        "absolute",
        "cwd",
        "expanduser",
        "home",
        "joinpath",
        "readlink",
        "relative_to",
        "rename",
        "replace",
        "resolve",
        "with_name",
        "with_segments",
        "with_stem",
        "with_suffix",
    }
)
_PATH_RETURNING_PROPERTIES: Final = frozenset({"parent"})
_PROCESS_CALLS: Final = frozenset(
    {
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "asyncio.subprocess.create_subprocess_exec",
        "asyncio.subprocess.create_subprocess_shell",
        "concurrent.futures.ProcessPoolExecutor",
        "concurrent.futures.process.ProcessPoolExecutor",
        "os.abort",
        "os.execl",
        "os.execle",
        "os.execlp",
        "os.execlpe",
        "os.execv",
        "os.execve",
        "os.execvp",
        "os.execvpe",
        "os.fork",
        "os.forkpty",
        "os.kill",
        "os.killpg",
        "os.login_tty",
        "os.popen",
        "os.posix_spawn",
        "os.posix_spawnp",
        "os.spawnl",
        "os.spawnle",
        "os.spawnlp",
        "os.spawnlpe",
        "os.spawnv",
        "os.spawnve",
        "os.spawnvp",
        "os.spawnvpe",
        "os.startfile",
        "os.system",
        "os.wait",
        "os.wait3",
        "os.wait4",
        "os.waitpid",
        "pty.fork",
        "pty.spawn",
    }
)
_PROCESS_LOOP_METHODS: Final = frozenset({"subprocess_exec", "subprocess_shell"})
_WILDCARD_PROHIBITIONS: Final = {
    "aifc": ("CAP009", "wildcard dependency", "aifc"),
    "anyio": ("CAP005", "wildcard dependency", "anyio"),
    "asyncio": ("CAP005", "wildcard dependency", "asyncio"),
    "asyncio.events": ("CAP001", "wildcard dependency", "asyncio.events"),
    "asyncio.streams": ("CAP005", "wildcard dependency", "asyncio.streams"),
    "asyncio.unix_events": ("CAP001", "wildcard dependency", "asyncio.unix_events"),
    "asyncio.windows_events": ("CAP001", "wildcard dependency", "asyncio.windows_events"),
    "concurrent.futures": ("CAP010", "wildcard dependency", "concurrent.futures"),
    "bz2": ("CAP009", "wildcard dependency", "bz2"),
    "codecs": ("CAP009", "wildcard dependency", "codecs"),
    "configparser": ("CAP009", "wildcard dependency", "configparser"),
    "dbm": ("CAP009", "wildcard dependency", "dbm"),
    "datetime": ("CAP001", "wildcard dependency", "datetime"),
    "filecmp": ("CAP009", "wildcard dependency", "filecmp"),
    "fileinput": ("CAP009", "wildcard dependency", "fileinput"),
    "getpass": ("CAP004", "wildcard dependency", "getpass"),
    "glob": ("CAP009", "wildcard dependency", "glob"),
    "google": ("CAP006", "wildcard dependency", "google"),
    "gzip": ("CAP009", "wildcard dependency", "gzip"),
    "http": ("CAP005", "wildcard dependency", "http"),
    "importlib.machinery": ("CAP009", "wildcard dependency", "importlib.machinery"),
    "importlib.resources": ("CAP009", "wildcard dependency", "importlib.resources"),
    "io": ("CAP009", "wildcard dependency", "io"),
    "linecache": ("CAP009", "wildcard dependency", "linecache"),
    "logging": ("CAP009", "wildcard dependency", "logging"),
    "lzma": ("CAP009", "wildcard dependency", "lzma"),
    "mailbox": ("CAP009", "wildcard dependency", "mailbox"),
    "mmap": ("CAP009", "wildcard dependency", "mmap"),
    "os": ("CAP004", "wildcard dependency", "os"),
    "os.path": ("CAP009", "wildcard dependency", "os.path"),
    "pathlib": ("CAP009", "wildcard dependency", "pathlib"),
    "pkgutil": ("CAP009", "wildcard dependency", "pkgutil"),
    "pty": ("CAP010", "wildcard dependency", "pty"),
    "random": ("CAP002", "wildcard dependency", "random"),
    "runpy": ("CAP009", "wildcard dependency", "runpy"),
    "secrets": ("CAP002", "wildcard dependency", "secrets"),
    "shelve": ("CAP009", "wildcard dependency", "shelve"),
    "shutil": ("CAP009", "wildcard dependency", "shutil"),
    "sndhdr": ("CAP009", "wildcard dependency", "sndhdr"),
    "sunau": ("CAP009", "wildcard dependency", "sunau"),
    "tarfile": ("CAP009", "wildcard dependency", "tarfile"),
    "tempfile": ("CAP009", "wildcard dependency", "tempfile"),
    "time": ("CAP001", "wildcard dependency", "time"),
    "tokenize": ("CAP009", "wildcard dependency", "tokenize"),
    "trio": ("CAP005", "wildcard dependency", "trio"),
    "urllib": ("CAP005", "wildcard dependency", "urllib"),
    "uuid": ("CAP003", "wildcard dependency", "uuid"),
    "wave": ("CAP009", "wildcard dependency", "wave"),
    "xml": ("CAP009", "wildcard dependency", "xml"),
    "zipfile": ("CAP009", "wildcard dependency", "zipfile"),
    "zipimport": ("CAP009", "wildcard dependency", "zipimport"),
}
_OPTIONAL_ANNOTATIONS: Final = frozenset({"Optional", "typing.Optional"})
_UNION_ANNOTATIONS: Final = frozenset({"Union", "typing.Union"})
_ANNOTATED_ANNOTATIONS: Final = frozenset({"Annotated", "typing.Annotated"})
_CLASSVAR_ANNOTATIONS: Final = frozenset({"ClassVar", "typing.ClassVar"})
_TYPE_ALIAS_ANNOTATIONS: Final = frozenset(
    {"TypeAlias", "typing.TypeAlias", "typing_extensions.TypeAlias"}
)
_ITERATION_BUILTIN_ARGUMENTS: Final = {
    "all": (0,),
    "any": (0,),
    "builtins.all": (0,),
    "builtins.any": (0,),
    "builtins.dict": (0,),
    "builtins.enumerate": (0,),
    "builtins.filter": (1,),
    "builtins.frozenset": (0,),
    "builtins.list": (0,),
    "builtins.map": (1,),
    "builtins.max": (0,),
    "builtins.min": (0,),
    "builtins.reversed": (0,),
    "builtins.set": (0,),
    "builtins.sorted": (0,),
    "builtins.sum": (0,),
    "builtins.tuple": (0,),
    "dict": (0,),
    "enumerate": (0,),
    "filter": (1,),
    "frozenset": (0,),
    "list": (0,),
    "map": (1,),
    "max": (0,),
    "min": (0,),
    "reversed": (0,),
    "set": (0,),
    "sorted": (0,),
    "sum": (0,),
    "tuple": (0,),
}
_EVENT_LOOP_TYPES: Final = frozenset(
    {
        "asyncio.AbstractEventLoop",
        "asyncio.BaseEventLoop",
        "asyncio.ProactorEventLoop",
        "asyncio.SelectorEventLoop",
        "asyncio.base_events.BaseEventLoop",
        "asyncio.events.AbstractEventLoop",
        "asyncio.proactor_events.BaseProactorEventLoop",
        "asyncio.selector_events.BaseSelectorEventLoop",
        "asyncio.unix_events.SelectorEventLoop",
        "asyncio.windows_events.ProactorEventLoop",
        "asyncio.windows_events.SelectorEventLoop",
    }
)
_EVENT_LOOP_POLICY_TYPES: Final = frozenset(
    {
        "asyncio.AbstractEventLoopPolicy",
        "asyncio.DefaultEventLoopPolicy",
        "asyncio.WindowsProactorEventLoopPolicy",
        "asyncio.WindowsSelectorEventLoopPolicy",
        "asyncio.events.AbstractEventLoopPolicy",
        "asyncio.events.BaseDefaultEventLoopPolicy",
        "asyncio.unix_events.DefaultEventLoopPolicy",
        "asyncio.windows_events.DefaultEventLoopPolicy",
        "asyncio.windows_events.WindowsProactorEventLoopPolicy",
        "asyncio.windows_events.WindowsSelectorEventLoopPolicy",
    }
)
_RUNNER_TYPES: Final = frozenset({"asyncio.Runner", "asyncio.runners.Runner"})
_TIMEDELTA_TYPES: Final = frozenset({"datetime.timedelta"})
_TRACKED_TYPES: Final = (
    _CONFIG_PARSER_TYPES
    | _EFFECT_RECEIVER_TYPES
    | _PATH_TYPES
    | _EVENT_LOOP_TYPES
    | _EVENT_LOOP_POLICY_TYPES
    | _RUNNER_TYPES
    | _TIMEDELTA_TYPES
    | frozenset({"datetime.date", "datetime.datetime", "random.Random"})
)


@dataclass(frozen=True, order=True)
class Violation:
    path: str
    line: int
    column: int
    rule_id: str
    kind: str
    subject: str

    def format(self) -> str:
        return (
            f"{self.path}:{self.line}:{self.column}: {self.rule_id} prohibited "
            f"{self.kind}: {self.subject}; governing rule: {GOVERNING_RULE}"
        )


@dataclass(frozen=True)
class _Binding:
    names: frozenset[str] = frozenset()
    types: frozenset[str] = frozenset()
    string_values: tuple[str, ...] = ()
    returns: _Binding | None = None
    can_be_none: bool = False
    can_be_naive: bool = False
    method_receiver_types: frozenset[str] = frozenset()
    method_receiver_can_be_naive: bool = False
    requires_seed: bool = False
    sequence_shapes: tuple[tuple[_Binding, ...], ...] = ()
    mapping_shapes: tuple[tuple[tuple[object, _Binding], ...], ...] = ()


@dataclass(frozen=True)
class _ParsedSource:
    relative_path: str
    module_name: str
    is_package: bool
    tree: ast.Module


@dataclass(frozen=True)
class _BlockJumps:
    fallthrough: dict[str, _Binding] | None
    breaks: tuple[dict[str, _Binding], ...] = ()
    continues: tuple[dict[str, _Binding], ...] = ()
    abrupts: tuple[dict[str, _Binding], ...] = ()


def protected_source_files(package_root: Path) -> tuple[Path, ...]:
    return tuple(
        source_file
        for source_file in sorted(package_root.rglob("*.py"))
        if source_file.relative_to(package_root).parts[0] not in EFFECT_ZONES
    )


def inspect_capability_dependencies(package_root: Path) -> tuple[Violation, ...]:
    violations: list[Violation] = []
    parsed_sources: list[_ParsedSource] = []
    for source_file in protected_source_files(package_root):
        relative_path = source_file.relative_to(package_root).as_posix()
        try:
            tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=relative_path)
        except (OSError, SyntaxError, UnicodeError):
            violations.append(
                Violation(relative_path, 1, 1, "CAP000", "invalid source", "Python source")
            )
            continue
        parsed_sources.append(
            _ParsedSource(
                relative_path=relative_path,
                module_name=_module_name(relative_path),
                is_package=relative_path.endswith("/__init__.py") or relative_path == "__init__.py",
                tree=tree,
            )
        )

    exports, class_fields = _build_static_bindings(parsed_sources)
    for source in parsed_sources:
        violations.extend(
            _CapabilityVisitor(
                source,
                exports,
                class_fields=class_fields,
            ).inspect()
        )
    return tuple(sorted(set(violations)))


def _merge_bindings(*bindings: _Binding) -> _Binding:
    present_returns = tuple(binding.returns for binding in bindings if binding.returns is not None)
    merged_return = _merge_bindings(*present_returns) if present_returns else None
    string_sequences = tuple(binding.string_values for binding in bindings if binding.string_values)
    string_values = (
        string_sequences[0]
        if string_sequences and all(value == string_sequences[0] for value in string_sequences)
        else ()
    )
    sequence_shapes: list[tuple[_Binding, ...]] = []
    mapping_shapes: list[tuple[tuple[object, _Binding], ...]] = []
    for binding in bindings:
        for sequence_shape in binding.sequence_shapes:
            if sequence_shape not in sequence_shapes:
                sequence_shapes.append(sequence_shape)
        for mapping_shape in binding.mapping_shapes:
            if mapping_shape not in mapping_shapes:
                mapping_shapes.append(mapping_shape)
    return _Binding(
        names=frozenset().union(*(binding.names for binding in bindings)),
        types=frozenset().union(*(binding.types for binding in bindings)),
        string_values=string_values,
        returns=merged_return,
        can_be_none=any(binding.can_be_none for binding in bindings),
        can_be_naive=any(binding.can_be_naive for binding in bindings),
        method_receiver_types=frozenset().union(
            *(binding.method_receiver_types for binding in bindings)
        ),
        method_receiver_can_be_naive=any(
            binding.method_receiver_can_be_naive for binding in bindings
        ),
        requires_seed=any(binding.requires_seed for binding in bindings),
        sequence_shapes=tuple(sequence_shapes),
        mapping_shapes=tuple(mapping_shapes),
    )


def _class_definition_binding(
    qualified_name: str,
    base_bindings: Iterable[_Binding],
) -> _Binding:
    bases = tuple(base_bindings)
    inherited_types = frozenset().union(
        *(base.types | (base.names & _TRACKED_TYPES) for base in bases)
    )
    return _Binding(
        names=frozenset({qualified_name}),
        types=frozenset({qualified_name}) | inherited_types,
        requires_seed=any(
            base.requires_seed or "random.Random" in (base.names | base.types) for base in bases
        ),
    )


def _merge_field_maps(*field_maps: dict[str, _Binding]) -> dict[str, _Binding]:
    names = frozenset().union(*(field_map.keys() for field_map in field_maps))
    return {
        name: _merge_bindings(*(field_map[name] for field_map in field_maps if name in field_map))
        for name in names
    }


def _fields_for_types(
    bindings: Iterable[_Binding],
    known_fields: dict[str, dict[str, _Binding]],
) -> dict[str, _Binding]:
    types = frozenset().union(*(binding.types for binding in bindings))
    return _merge_field_maps(
        *(known_fields[type_name] for type_name in types if type_name in known_fields)
    )


def _module_name(relative_path: str) -> str:
    parts = relative_path.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join((PACKAGE_NAME, *parts))


def _resolve_import_module(
    source: _ParsedSource,
    *,
    module: str | None,
    level: int,
) -> str:
    if level == 0:
        return module or ""
    package_parts = source.module_name.split(".")
    if not source.is_package:
        package_parts.pop()
    ascend = level - 1
    if ascend > len(package_parts):
        return module or ""
    base = package_parts[: len(package_parts) - ascend]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def _build_static_bindings(
    sources: Sequence[_ParsedSource],
) -> tuple[dict[str, dict[str, _Binding]], dict[str, dict[str, _Binding]]]:
    definition_count = sum(
        len(_class_definitions(source.tree.body, source.module_name)) for source in sources
    )
    exports: dict[str, dict[str, _Binding]] = {}
    class_fields: dict[str, dict[str, _Binding]] = {}
    for _ in range(max(1, len(sources) + definition_count + 1)):
        updated_exports = _build_module_exports(sources, class_fields)
        updated_fields = _build_class_fields(sources, updated_exports)
        if updated_exports == exports and updated_fields == class_fields:
            return updated_exports, updated_fields
        exports = updated_exports
        class_fields = updated_fields
    return exports, class_fields


def _build_module_exports(
    sources: Sequence[_ParsedSource],
    class_fields: dict[str, dict[str, _Binding]],
) -> dict[str, dict[str, _Binding]]:
    exports: dict[str, dict[str, _Binding]] = {source.module_name: {} for source in sources}
    for _ in range(max(1, len(sources) + 1)):
        updated = {
            source.module_name: _collect_module_exports(source, exports, class_fields)
            for source in sources
        }
        if updated == exports:
            return updated
        exports = updated
    return exports


def _build_class_fields(
    sources: Sequence[_ParsedSource],
    exports: dict[str, dict[str, _Binding]],
) -> dict[str, dict[str, _Binding]]:
    definitions = tuple(
        (source, statement, qualified_name)
        for source in sources
        for statement, qualified_name in _class_definitions(source.tree.body, source.module_name)
    )
    fields: dict[str, dict[str, _Binding]] = {}
    for _ in range(max(1, len(definitions) + 1)):
        updated: dict[str, dict[str, _Binding]] = {}
        for source, statement, qualified_name in definitions:
            visible = exports.get(source.module_name, {})
            base_bindings = tuple(
                _static_expression_binding_with_exports(base, visible, exports)
                for base in statement.bases
            )
            inherited = _fields_for_types(base_bindings, fields)
            updated[qualified_name] = _merge_field_maps(
                updated.get(qualified_name, {}),
                inherited,
                _class_field_bindings(
                    source,
                    statement,
                    qualified_name,
                    visible,
                    exports,
                    fields,
                ),
            )
        if updated == fields:
            return updated
        fields = updated
    return fields


def _class_definitions(
    statements: list[ast.stmt],
    owner: str,
) -> tuple[tuple[ast.ClassDef, str], ...]:
    definitions: list[tuple[ast.ClassDef, str]] = []
    for statement in statements:
        if isinstance(statement, ast.ClassDef):
            qualified_name = f"{owner}.{statement.name}"
            definitions.append((statement, qualified_name))
            definitions.extend(_class_definitions(statement.body, qualified_name))
            continue
        for block in _module_control_flow_blocks(statement):
            definitions.extend(_class_definitions(block, owner))
    return tuple(definitions)


def _collect_module_exports(
    source: _ParsedSource,
    exports: dict[str, dict[str, _Binding]],
    class_fields: dict[str, dict[str, _Binding]],
) -> dict[str, _Binding]:
    return _collect_statement_exports(source, source.tree.body, exports, class_fields, {})


def _collect_statement_exports(
    source: _ParsedSource,
    statements: list[ast.stmt],
    exports: dict[str, dict[str, _Binding]],
    class_fields: dict[str, dict[str, _Binding]],
    initial: dict[str, _Binding],
) -> dict[str, _Binding]:
    bindings = dict(initial)
    for statement in statements:
        binding_for = partial(
            _static_expression_binding_with_exports,
            bindings=bindings,
            exports=exports,
            class_fields=class_fields,
        )
        _record_named_expressions(
            _non_control_statement_expressions(statement), bindings, binding_for
        )
        direct = _direct_statement_exports(source, statement, exports, class_fields, bindings)
        if direct is not None:
            bindings.update(direct)
            continue
        if isinstance(statement, ast.Try | ast.TryStar):
            bindings = _collect_try_exports(source, statement, exports, class_fields, bindings)
            continue
        if isinstance(statement, ast.For | ast.AsyncFor | ast.While):
            bindings = _collect_loop_exports(source, statement, exports, class_fields, bindings)
            continue
        branches = _control_flow_branches(statement, bindings, exports, class_fields)
        if branches:
            branch_results = tuple(
                _collect_statement_exports(source, block, exports, class_fields, branch_scope)
                for block, branch_scope in branches
            )
            bindings = _join_scopes(bindings, *branch_results)
    return bindings


def _collect_try_exports(
    source: _ParsedSource,
    statement: ast.Try | ast.TryStar,
    exports: dict[str, dict[str, _Binding]],
    class_fields: dict[str, dict[str, _Binding]],
    initial: dict[str, _Binding],
) -> dict[str, _Binding]:
    body = _collect_statement_exports(source, statement.body, exports, class_fields, initial)
    success = _collect_statement_exports(source, statement.orelse, exports, class_fields, body)
    body_prefixes = _possible_prefix_scopes(
        source,
        statement.body,
        exports,
        class_fields,
        initial,
    )
    possible_handler_input = _join_scopes(*body_prefixes)
    handlers: list[dict[str, _Binding]] = []
    for handler in statement.handlers:
        handler_input = dict(possible_handler_input)
        if handler.name is not None:
            handler_input[handler.name] = _Binding()
        handlers.append(
            _collect_statement_exports(source, handler.body, exports, class_fields, handler_input)
        )
    joined = _join_scopes(initial, success, *handlers)
    return _collect_statement_exports(source, statement.finalbody, exports, class_fields, joined)


def _possible_prefix_scopes(
    source: _ParsedSource,
    statements: list[ast.stmt],
    exports: dict[str, dict[str, _Binding]],
    class_fields: dict[str, dict[str, _Binding]],
    initial: dict[str, _Binding],
) -> tuple[dict[str, _Binding], ...]:
    current = dict(initial)
    prefixes: list[dict[str, _Binding]] = [current]
    for statement in statements:
        branch_seed = dict(current)
        branches = _control_flow_branches(statement, branch_seed, exports, class_fields)
        for block, branch_scope in branches:
            prefixes.extend(
                _possible_prefix_scopes(
                    source,
                    block,
                    exports,
                    class_fields,
                    branch_scope,
                )
            )
        current = _collect_statement_exports(
            source,
            [statement],
            exports,
            class_fields,
            current,
        )
        prefixes.append(current)
    return tuple(prefixes)


def _collect_loop_exports(
    source: _ParsedSource,
    statement: ast.For | ast.AsyncFor | ast.While,
    exports: dict[str, dict[str, _Binding]],
    class_fields: dict[str, dict[str, _Binding]],
    initial: dict[str, _Binding],
) -> dict[str, _Binding]:
    before_loop = dict(initial)
    binding_for = partial(
        _static_expression_binding_with_exports,
        bindings=before_loop,
        exports=exports,
        class_fields=class_fields,
    )
    header = statement.iter if isinstance(statement, ast.For | ast.AsyncFor) else statement.test
    _record_named_expressions((header,), before_loop, binding_for)
    repeated = dict(before_loop)
    break_scopes: list[dict[str, _Binding]] = []
    for _ in range(max(1, len(statement.body) + len(before_loop) + 1)):
        body_input = dict(repeated)
        if isinstance(statement, ast.For | ast.AsyncFor):
            body_binding_for = partial(
                _static_expression_binding_with_exports,
                bindings=body_input,
                exports=exports,
                class_fields=class_fields,
            )
            for target, binding in _iterated_target_bindings(
                statement.target, statement.iter, body_binding_for
            ):
                body_input.update(_scope_target_bindings(target, binding))
        jumps = _collect_block_jumps(
            source,
            statement.body,
            exports,
            class_fields,
            body_input,
        )
        break_scopes.extend(jumps.breaks)
        back_edges = (
            *((jumps.fallthrough,) if jumps.fallthrough is not None else ()),
            *jumps.continues,
        )
        updated = _join_scopes(before_loop, *back_edges)
        if updated == repeated:
            break
        repeated = updated
    after_else = _collect_statement_exports(
        source, statement.orelse, exports, class_fields, repeated
    )
    return _join_scopes(after_else, *break_scopes)


def _direct_statement_exports(
    source: _ParsedSource,
    statement: ast.stmt,
    exports: dict[str, dict[str, _Binding]],
    class_fields: dict[str, dict[str, _Binding]],
    bindings: dict[str, _Binding],
) -> dict[str, _Binding] | None:
    if isinstance(statement, ast.Import | ast.ImportFrom):
        return _import_statement_exports(source, statement, exports)
    if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
        return {
            statement.name: _Binding(
                names=frozenset({f"{source.module_name}.{statement.name}"}),
                returns=_annotation_binding(statement.returns, bindings, exports),
            )
        }
    if isinstance(statement, ast.ClassDef):
        qualified_name = f"{source.module_name}.{statement.name}"
        return {
            statement.name: _class_definition_binding(
                qualified_name,
                (
                    _static_expression_binding_with_exports(base, bindings, exports)
                    for base in statement.bases
                ),
            )
        }
    if isinstance(statement, ast.Assign):
        binding_for = partial(
            _static_expression_binding_with_exports,
            bindings=bindings,
            exports=exports,
            class_fields=class_fields,
        )
        assignments = (
            assignment
            for target in statement.targets
            for assignment in _assignment_bindings(target, statement.value, binding_for)
        )
        return {
            name: binding
            for target, binding in assignments
            if (name := _assignment_name(target)) is not None
        }
    if isinstance(statement, ast.AnnAssign | ast.TypeAlias):
        return _module_annotation_bindings(statement, bindings, exports, class_fields)
    return None


def _import_statement_exports(
    source: _ParsedSource,
    statement: ast.Import | ast.ImportFrom,
    exports: dict[str, dict[str, _Binding]],
) -> dict[str, _Binding]:
    if isinstance(statement, ast.Import):
        return {
            alias.asname or alias.name.split(".")[0]: _Binding(
                names=frozenset({alias.name if alias.asname else alias.name.split(".")[0]})
            )
            for alias in statement.names
        }
    module = _resolve_import_module(source, module=statement.module, level=statement.level)
    if len(statement.names) == 1 and statement.names[0].name == "*":
        return dict(exports.get(module, {}))
    return {
        alias.asname or alias.name: exports.get(module, {}).get(alias.name)
        or _Binding(names=frozenset({f"{module}.{alias.name}"}))
        for alias in statement.names
    }


def _module_control_flow_blocks(statement: ast.stmt) -> tuple[list[ast.stmt], ...]:
    if isinstance(statement, ast.If | ast.For | ast.AsyncFor | ast.While):
        return (statement.body, statement.orelse)
    if isinstance(statement, ast.Match):
        return tuple(case.body for case in statement.cases)
    if isinstance(statement, ast.Try | ast.TryStar):
        return (
            statement.body,
            statement.orelse,
            statement.finalbody,
            *(handler.body for handler in statement.handlers),
        )
    if isinstance(statement, ast.With | ast.AsyncWith):
        return (statement.body,)
    return ()


def _non_control_statement_expressions(  # noqa: PLR0911
    statement: ast.stmt,
) -> tuple[ast.AST | None, ...]:
    if isinstance(statement, ast.Assign):
        return (statement.value,)
    if isinstance(statement, ast.AnnAssign | ast.AugAssign | ast.TypeAlias):
        return (statement.value,)
    if isinstance(statement, ast.Expr | ast.Return):
        return (statement.value,)
    if isinstance(statement, ast.Raise):
        return (statement.exc, statement.cause)
    if isinstance(statement, ast.Assert):
        return (statement.test, statement.msg)
    if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
        return (
            *statement.decorator_list,
            *statement.args.defaults,
            *statement.args.kw_defaults,
        )
    if isinstance(statement, ast.ClassDef):
        return (
            *statement.decorator_list,
            *statement.bases,
            *(keyword.value for keyword in statement.keywords),
        )
    return ()


def _record_named_expressions(
    expressions: Iterable[ast.AST | None],
    scope: dict[str, _Binding],
    binding_for: Callable[[ast.AST], _Binding],
) -> dict[str, _Binding]:
    recorded: dict[str, _Binding] = {}

    def visit_comprehension(
        expression: ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp,
    ) -> None:
        results = (
            (expression.key, expression.value)
            if isinstance(expression, ast.DictComp)
            else (expression.elt,)
        )
        limit = max(
            1,
            len(scope)
            + len(expression.generators)
            + sum(len(generator.ifs) for generator in expression.generators)
            + len(results)
            + 1,
        )
        for _ in range(limit):
            before = dict(scope)
            saved_targets: dict[str, _Binding | None] = {}
            for generator in expression.generators:
                visit(generator.iter)
                for target, binding in _iterated_target_bindings(
                    generator.target, generator.iter, binding_for
                ):
                    for name, target_binding in _scope_target_bindings(target, binding).items():
                        if name not in saved_targets:
                            saved_targets[name] = scope.get(name)
                        scope[name] = target_binding
                for condition in generator.ifs:
                    visit(condition)
            for result in results:
                visit(result)
            for name, previous in saved_targets.items():
                if previous is None:
                    scope.pop(name, None)
                else:
                    scope[name] = previous
            if scope == before:
                break

    def visit(expression: ast.AST | None) -> None:
        if expression is None or isinstance(expression, ast.Lambda):
            return
        if isinstance(expression, ast.NamedExpr):
            visit(expression.value)
            updates = _scope_target_bindings(expression.target, binding_for(expression.value))
            scope.update(updates)
            recorded.update(updates)
            return
        if isinstance(expression, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
            visit_comprehension(expression)
            return
        for child in ast.iter_child_nodes(expression):
            visit(child)

    for expression in expressions:
        visit(expression)
    return recorded


def _scope_target_bindings(target: ast.AST, binding: _Binding) -> dict[str, _Binding]:
    if isinstance(target, ast.Name):
        return {target.id: binding}
    if isinstance(target, ast.Starred):
        return _scope_target_bindings(target.value, binding)
    if isinstance(target, ast.Tuple | ast.List):
        updates: dict[str, _Binding] = {}
        for element in target.elts:
            updates.update(_scope_target_bindings(element, binding))
        return updates
    return {}


def _control_flow_branches(
    statement: ast.stmt,
    scope: dict[str, _Binding],
    exports: dict[str, dict[str, _Binding]],
    class_fields: dict[str, dict[str, _Binding]],
) -> tuple[tuple[list[ast.stmt], dict[str, _Binding]], ...]:
    binding_for = partial(
        _static_expression_binding_with_exports,
        bindings=scope,
        exports=exports,
        class_fields=class_fields,
    )
    if isinstance(statement, ast.If | ast.While):
        _record_named_expressions((statement.test,), scope, binding_for)
        return ((statement.body, dict(scope)), (statement.orelse, dict(scope)))
    if isinstance(statement, ast.For | ast.AsyncFor):
        _record_named_expressions((statement.iter,), scope, binding_for)
        body_scope = dict(scope)
        for target, binding in _iterated_target_bindings(
            statement.target, statement.iter, binding_for
        ):
            body_scope.update(_scope_target_bindings(target, binding))
        return ((statement.body, body_scope), (statement.orelse, dict(scope)))
    if isinstance(statement, ast.With | ast.AsyncWith):
        for item in statement.items:
            _record_named_expressions((item.context_expr,), scope, binding_for)
            if item.optional_vars is not None:
                scope.update(
                    _scope_target_bindings(item.optional_vars, binding_for(item.context_expr))
                )
        return ((statement.body, dict(scope)),)
    if isinstance(statement, ast.Match):
        _record_named_expressions((statement.subject,), scope, binding_for)
        subject = binding_for(statement.subject)
        branches: list[tuple[list[ast.stmt], dict[str, _Binding]]] = []
        for case in statement.cases:
            case_scope = dict(scope)
            case_scope.update(
                _pattern_capture_bindings(
                    case.pattern,
                    statement.subject,
                    subject,
                    binding_for,
                    class_fields,
                )
            )
            case_binding_for = partial(
                _static_expression_binding_with_exports,
                bindings=case_scope,
                exports=exports,
                class_fields=class_fields,
            )
            _record_named_expressions((case.guard,), case_scope, case_binding_for)
            branches.append((case.body, case_scope))
        return tuple(branches)
    blocks = _module_control_flow_blocks(statement)
    return tuple((block, dict(scope)) for block in blocks)


def _collect_block_jumps(
    source: _ParsedSource,
    statements: list[ast.stmt],
    exports: dict[str, dict[str, _Binding]],
    class_fields: dict[str, dict[str, _Binding]],
    initial: dict[str, _Binding],
) -> _BlockJumps:
    current: dict[str, _Binding] | None = dict(initial)
    breaks: list[dict[str, _Binding]] = []
    continues: list[dict[str, _Binding]] = []
    abrupts: list[dict[str, _Binding]] = []
    for statement in statements:
        if current is None:
            break
        if isinstance(statement, ast.Break):
            breaks.append(current)
            current = None
            break
        if isinstance(statement, ast.Continue):
            continues.append(current)
            current = None
            break
        if isinstance(statement, ast.Return | ast.Raise):
            binding_for = partial(
                _static_expression_binding_with_exports,
                bindings=current,
                exports=exports,
                class_fields=class_fields,
            )
            _record_named_expressions(
                _non_control_statement_expressions(statement),
                current,
                binding_for,
            )
            abrupts.append(current)
            current = None
            break
        if isinstance(statement, ast.For | ast.AsyncFor | ast.While):
            current = _collect_statement_exports(
                source,
                [statement],
                exports,
                class_fields,
                current,
            )
            continue

        if isinstance(statement, ast.Try | ast.TryStar):
            outcome = _collect_try_jumps(
                source,
                statement,
                exports,
                class_fields,
                current,
            )
            breaks.extend(outcome.breaks)
            continues.extend(outcome.continues)
            abrupts.extend(outcome.abrupts)
            current = outcome.fallthrough
            continue

        branch_seed = dict(current)
        branches = _control_flow_branches(statement, branch_seed, exports, class_fields)
        if not branches:
            current = _collect_statement_exports(
                source,
                [statement],
                exports,
                class_fields,
                current,
            )
            continue

        outcomes = tuple(
            _collect_block_jumps(
                source,
                block,
                exports,
                class_fields,
                branch_scope,
            )
            for block, branch_scope in branches
        )
        breaks.extend(scope for outcome in outcomes for scope in outcome.breaks)
        continues.extend(scope for outcome in outcomes for scope in outcome.continues)
        abrupts.extend(scope for outcome in outcomes for scope in outcome.abrupts)
        if isinstance(statement, ast.If | ast.Match | ast.With | ast.AsyncWith):
            fallthroughs = [
                outcome.fallthrough for outcome in outcomes if outcome.fallthrough is not None
            ]
            if isinstance(statement, ast.Match):
                fallthroughs.append(current)
            current = _join_scopes(*fallthroughs) if fallthroughs else None
            continue

        current = _collect_statement_exports(
            source,
            [statement],
            exports,
            class_fields,
            current,
        )
    return _BlockJumps(current, tuple(breaks), tuple(continues), tuple(abrupts))


def _collect_try_jumps(
    source: _ParsedSource,
    statement: ast.Try | ast.TryStar,
    exports: dict[str, dict[str, _Binding]],
    class_fields: dict[str, dict[str, _Binding]],
    initial: dict[str, _Binding],
) -> _BlockJumps:
    body = _collect_block_jumps(
        source,
        statement.body,
        exports,
        class_fields,
        initial,
    )
    successes = (
        (
            _collect_block_jumps(
                source,
                statement.orelse,
                exports,
                class_fields,
                body.fallthrough,
            ),
        )
        if body.fallthrough is not None
        else ()
    )
    handler_input = _join_scopes(
        *_possible_prefix_scopes(
            source,
            statement.body,
            exports,
            class_fields,
            initial,
        )
    )
    handlers = tuple(
        _collect_block_jumps(
            source,
            handler.body,
            exports,
            class_fields,
            handler_input | ({handler.name: _Binding()} if handler.name is not None else {}),
        )
        for handler in statement.handlers
    )
    outcomes = (*successes, *handlers)
    normal = tuple(outcome.fallthrough for outcome in outcomes if outcome.fallthrough is not None)
    raw = _BlockJumps(
        _join_scopes(*normal) if normal else None,
        (*body.breaks, *(scope for outcome in outcomes for scope in outcome.breaks)),
        (*body.continues, *(scope for outcome in outcomes for scope in outcome.continues)),
        (*body.abrupts, *(scope for outcome in outcomes for scope in outcome.abrupts)),
    )
    return _apply_finally_to_jumps(
        source,
        statement.finalbody,
        exports,
        class_fields,
        raw,
    )


def _apply_finally_to_jumps(
    source: _ParsedSource,
    finalbody: list[ast.stmt],
    exports: dict[str, dict[str, _Binding]],
    class_fields: dict[str, dict[str, _Binding]],
    incoming: _BlockJumps,
) -> _BlockJumps:
    if not finalbody:
        return incoming
    fallthroughs: list[dict[str, _Binding]] = []
    breaks: list[dict[str, _Binding]] = []
    continues: list[dict[str, _Binding]] = []
    abrupts: list[dict[str, _Binding]] = []
    inputs = (
        *((incoming.fallthrough,) if incoming.fallthrough is not None else ()),
        *incoming.breaks,
        *incoming.continues,
        *incoming.abrupts,
    )
    for index, scope in enumerate(inputs):
        outcome = _collect_block_jumps(
            source,
            finalbody,
            exports,
            class_fields,
            scope,
        )
        breaks.extend(outcome.breaks)
        continues.extend(outcome.continues)
        abrupts.extend(outcome.abrupts)
        if outcome.fallthrough is None:
            continue
        if incoming.fallthrough is not None and index == 0:
            fallthroughs.append(outcome.fallthrough)
        elif index < (1 if incoming.fallthrough is not None else 0) + len(incoming.breaks):
            breaks.append(outcome.fallthrough)
        elif index < (
            (1 if incoming.fallthrough is not None else 0)
            + len(incoming.breaks)
            + len(incoming.continues)
        ):
            continues.append(outcome.fallthrough)
        else:
            abrupts.append(outcome.fallthrough)
    return _BlockJumps(
        _join_scopes(*fallthroughs) if fallthroughs else None,
        tuple(breaks),
        tuple(continues),
        tuple(abrupts),
    )


def _pattern_capture_bindings(
    pattern: ast.pattern,
    subject_expression: ast.AST | None,
    subject: _Binding,
    binding_for: Callable[[ast.AST], _Binding],
    class_fields: dict[str, dict[str, _Binding]],
) -> dict[str, _Binding]:
    if isinstance(pattern, ast.MatchAs):
        nested = (
            _pattern_capture_bindings(
                pattern.pattern,
                subject_expression,
                subject,
                binding_for,
                class_fields,
            )
            if pattern.pattern is not None
            else {}
        )
        if pattern.name is not None:
            nested[pattern.name] = _merge_bindings(nested.get(pattern.name, _Binding()), subject)
        return nested
    if isinstance(pattern, ast.MatchStar):
        return {pattern.name: subject} if pattern.name is not None else {}
    if isinstance(pattern, ast.MatchOr):
        return _merge_binding_maps(
            *(
                _pattern_capture_bindings(
                    alternative,
                    subject_expression,
                    subject,
                    binding_for,
                    class_fields,
                )
                for alternative in pattern.patterns
            )
        )
    if isinstance(pattern, ast.MatchSequence):
        sequence_captures: list[dict[str, _Binding]] = []
        if isinstance(subject_expression, ast.Tuple | ast.List):
            sequence_captures.append(
                _sequence_pattern_bindings(
                    pattern.patterns,
                    subject_expression.elts,
                    binding_for,
                    class_fields,
                )
            )
        sequence_captures.extend(
            _sequence_pattern_binding_values(
                pattern.patterns,
                shape,
                binding_for,
                class_fields,
            )
            for shape in subject.sequence_shapes
        )
        if sequence_captures:
            return _merge_binding_maps(*sequence_captures)
    if isinstance(pattern, ast.MatchMapping) and (
        isinstance(subject_expression, ast.Dict) or subject.mapping_shapes
    ):
        mapping_captures = _merge_binding_maps(
            *(
                _pattern_capture_bindings(
                    nested_pattern,
                    value,
                    binding_for(value),
                    binding_for,
                    class_fields,
                )
                for key, nested_pattern in zip(pattern.keys, pattern.patterns, strict=True)
                for value in (
                    _matching_dict_values(key, subject_expression)
                    if isinstance(subject_expression, ast.Dict)
                    else ()
                )
            ),
            *(
                _pattern_capture_bindings(
                    nested_pattern,
                    None,
                    value,
                    binding_for,
                    class_fields,
                )
                for key, nested_pattern in zip(pattern.keys, pattern.patterns, strict=True)
                for value in _matching_mapping_bindings(key, subject)
            ),
        )
        if pattern.rest is not None:
            mapping_captures[pattern.rest] = _Binding()
        return mapping_captures
    return (
        _class_pattern_capture_bindings(pattern, subject, binding_for, class_fields)
        if isinstance(pattern, ast.MatchClass)
        else _fallback_pattern_capture_bindings(pattern, subject)
    )


def _class_pattern_capture_bindings(
    pattern: ast.MatchClass,
    subject: _Binding,
    binding_for: Callable[[ast.AST], _Binding],
    class_fields: dict[str, dict[str, _Binding]],
) -> dict[str, _Binding]:
    class_binding = binding_for(pattern.cls)
    matched = _merge_bindings(
        subject,
        _Binding(types=class_binding.types | (class_binding.names & _TRACKED_TYPES)),
    )
    fields = _fields_for_types((matched,), class_fields)
    match_args = fields.get("__match_args__", _Binding()).string_values
    positional = _merge_binding_maps(
        *(
            _pattern_capture_bindings(
                nested,
                None,
                fields.get(match_args[index], matched) if index < len(match_args) else matched,
                binding_for,
                class_fields,
            )
            for index, nested in enumerate(pattern.patterns)
        )
    )
    keyword = _merge_binding_maps(
        *(
            _pattern_capture_bindings(
                nested,
                None,
                fields.get(attribute, matched),
                binding_for,
                class_fields,
            )
            for attribute, nested in zip(pattern.kwd_attrs, pattern.kwd_patterns, strict=True)
        )
    )
    return _merge_binding_maps(positional, keyword)


def _fallback_pattern_capture_bindings(
    pattern: ast.pattern, subject: _Binding
) -> dict[str, _Binding]:
    names = {
        node.name
        for node in ast.walk(pattern)
        if isinstance(node, ast.MatchAs | ast.MatchStar) and node.name is not None
    }
    names.update(
        node.rest
        for node in ast.walk(pattern)
        if isinstance(node, ast.MatchMapping) and node.rest is not None
    )
    return dict.fromkeys(names, subject)


def _sequence_pattern_bindings(
    patterns: list[ast.pattern],
    values: list[ast.expr],
    binding_for: Callable[[ast.AST], _Binding],
    class_fields: dict[str, dict[str, _Binding]],
) -> dict[str, _Binding]:
    starred = tuple(
        index for index, pattern in enumerate(patterns) if isinstance(pattern, ast.MatchStar)
    )
    if not starred and len(patterns) == len(values):
        return _merge_binding_maps(
            *(
                _pattern_capture_bindings(
                    pattern,
                    value,
                    binding_for(value),
                    binding_for,
                    class_fields,
                )
                for pattern, value in zip(patterns, values, strict=True)
            )
        )
    if len(starred) == 1 and len(values) >= len(patterns) - 1:
        starred_index = starred[0]
        trailing = len(patterns) - starred_index - 1
        leading_bindings = tuple(
            _pattern_capture_bindings(
                pattern,
                value,
                binding_for(value),
                binding_for,
                class_fields,
            )
            for pattern, value in zip(patterns[:starred_index], values[:starred_index], strict=True)
        )
        trailing_bindings = tuple(
            _pattern_capture_bindings(
                pattern,
                value,
                binding_for(value),
                binding_for,
                class_fields,
            )
            for pattern, value in zip(
                patterns[-trailing:] if trailing else (),
                values[-trailing:] if trailing else (),
                strict=True,
            )
        )
        middle = values[starred_index : len(values) - trailing if trailing else None]
        star_bindings = _pattern_capture_bindings(
            patterns[starred_index],
            None,
            _merge_bindings(*(binding_for(value) for value in middle)),
            binding_for,
            class_fields,
        )
        return _merge_binding_maps(*leading_bindings, star_bindings, *trailing_bindings)
    return {}


def _sequence_pattern_binding_values(
    patterns: list[ast.pattern],
    values: tuple[_Binding, ...],
    binding_for: Callable[[ast.AST], _Binding],
    class_fields: dict[str, dict[str, _Binding]],
) -> dict[str, _Binding]:
    starred = tuple(
        index for index, pattern in enumerate(patterns) if isinstance(pattern, ast.MatchStar)
    )
    if not starred and len(patterns) == len(values):
        pairs = tuple(zip(patterns, values, strict=True))
    elif len(starred) == 1 and len(values) >= len(patterns) - 1:
        starred_index = starred[0]
        trailing = len(patterns) - starred_index - 1
        pairs = tuple(zip(patterns[:starred_index], values[:starred_index], strict=True))
        if trailing:
            pairs += tuple(zip(patterns[-trailing:], values[-trailing:], strict=True))
        middle = values[starred_index : len(values) - trailing if trailing else None]
        pairs += ((patterns[starred_index], _merge_bindings(*middle)),)
    else:
        return {}
    return _merge_binding_maps(
        *(
            _pattern_capture_bindings(
                nested_pattern,
                None,
                value,
                binding_for,
                class_fields,
            )
            for nested_pattern, value in pairs
        )
    )


def _merge_binding_maps(*maps: dict[str, _Binding]) -> dict[str, _Binding]:
    names = frozenset().union(*(mapping.keys() for mapping in maps))
    return {
        name: _merge_bindings(*(mapping[name] for mapping in maps if name in mapping))
        for name in names
    }


def _iterated_target_bindings(
    target: ast.AST,
    expression: ast.expr,
    binding_for: Callable[[ast.AST], _Binding],
) -> tuple[tuple[ast.AST, _Binding], ...]:
    values: tuple[ast.expr, ...]
    if isinstance(expression, ast.Tuple | ast.List | ast.Set):
        values = tuple(expression.elts)
    elif isinstance(expression, ast.Dict):
        values = tuple(key for key in expression.keys if key is not None)
    else:
        return _target_bindings(target, _Binding())
    bindings_by_target: dict[ast.AST, list[_Binding]] = {}
    for value in values:
        for assignment_target, binding in _assignment_bindings(target, value, binding_for):
            bindings_by_target.setdefault(assignment_target, []).append(binding)
    return tuple(
        (assignment_target, _merge_bindings(*bindings))
        for assignment_target, bindings in bindings_by_target.items()
    )


def _iterated_value_expressions(expression: ast.expr) -> tuple[ast.expr, ...]:
    if isinstance(expression, ast.Tuple | ast.List | ast.Set):
        return tuple(expression.elts)
    if isinstance(expression, ast.Dict):
        return tuple(key for key in expression.keys if key is not None)
    return ()


def _unpacking_protocol_values(target: ast.AST, value: ast.expr) -> tuple[ast.expr, ...]:
    if not isinstance(target, ast.Tuple | ast.List):
        return ()
    unpacked: list[ast.expr] = [value]
    if isinstance(value, ast.Set | ast.Dict):
        candidates = _iterated_value_expressions(value)
        for candidate_target in target.elts:
            for candidate in candidates:
                unpacked.extend(_unpacking_protocol_values(candidate_target, candidate))
        return tuple(unpacked)
    if not isinstance(value, ast.Tuple | ast.List):
        return tuple(unpacked)
    starred = tuple(
        index for index, element in enumerate(target.elts) if isinstance(element, ast.Starred)
    )
    pairs: tuple[tuple[ast.AST, ast.expr], ...] = ()
    if not starred and len(target.elts) == len(value.elts):
        pairs = tuple(zip(target.elts, value.elts, strict=True))
    elif len(starred) == 1 and len(value.elts) >= len(target.elts) - 1:
        starred_index = starred[0]
        trailing = len(target.elts) - starred_index - 1
        pairs = tuple(zip(target.elts[:starred_index], value.elts[:starred_index], strict=True))
        if trailing:
            pairs += tuple(zip(target.elts[-trailing:], value.elts[-trailing:], strict=True))
    for nested_target, nested_value in pairs:
        unpacked.extend(_unpacking_protocol_values(nested_target, nested_value))
    return tuple(unpacked)


def _aligned_sequence_expressions(
    patterns: list[ast.pattern],
    values: list[ast.expr],
) -> tuple[tuple[ast.pattern, ast.expr], ...]:
    starred = tuple(
        index for index, nested in enumerate(patterns) if isinstance(nested, ast.MatchStar)
    )
    if not starred and len(patterns) == len(values):
        return tuple(zip(patterns, values, strict=True))
    if len(starred) != 1 or len(values) < len(patterns) - 1:
        return ()
    starred_index = starred[0]
    trailing = len(patterns) - starred_index - 1
    pairs = tuple(zip(patterns[:starred_index], values[:starred_index], strict=True))
    if trailing:
        pairs += tuple(zip(patterns[-trailing:], values[-trailing:], strict=True))
    return pairs


def _aligned_sequence_bindings(
    patterns: list[ast.pattern],
    values: tuple[_Binding, ...],
) -> tuple[tuple[ast.pattern, _Binding], ...]:
    starred = tuple(
        index for index, nested in enumerate(patterns) if isinstance(nested, ast.MatchStar)
    )
    if not starred and len(patterns) == len(values):
        return tuple(zip(patterns, values, strict=True))
    if len(starred) != 1 or len(values) < len(patterns) - 1:
        return ()
    starred_index = starred[0]
    trailing = len(patterns) - starred_index - 1
    pairs = tuple(zip(patterns[:starred_index], values[:starred_index], strict=True))
    if trailing:
        pairs += tuple(zip(patterns[-trailing:], values[-trailing:], strict=True))
    return pairs


def _pattern_effect_receivers(
    pattern: ast.pattern,
    subject_expression: ast.expr | None,
    subject: _Binding,
    binding_for: Callable[[ast.AST | None], _Binding],
    class_fields: dict[str, dict[str, _Binding]],
) -> tuple[tuple[ast.pattern, _Binding, str], ...]:
    if isinstance(pattern, ast.MatchAs) and pattern.pattern is not None:
        return _pattern_effect_receivers(
            pattern.pattern, subject_expression, subject, binding_for, class_fields
        )
    if isinstance(pattern, ast.MatchOr):
        return tuple(
            receiver
            for alternative in pattern.patterns
            for receiver in _pattern_effect_receivers(
                alternative, subject_expression, subject, binding_for, class_fields
            )
        )
    if isinstance(pattern, ast.MatchSequence):
        receivers: tuple[tuple[ast.pattern, _Binding, str], ...] = ((pattern, subject, "__iter__"),)
        pairs = (
            _aligned_sequence_expressions(pattern.patterns, subject_expression.elts)
            if isinstance(subject_expression, ast.Tuple | ast.List)
            else ()
        )
        return (
            *receivers,
            *(
                receiver
                for nested_pattern, nested_subject in pairs
                for receiver in _pattern_effect_receivers(
                    nested_pattern,
                    nested_subject,
                    binding_for(nested_subject),
                    binding_for,
                    class_fields,
                )
            ),
            *(
                receiver
                for shape in subject.sequence_shapes
                for nested_pattern, nested_subject in _aligned_sequence_bindings(
                    pattern.patterns, shape
                )
                for receiver in _pattern_effect_receivers(
                    nested_pattern,
                    None,
                    nested_subject,
                    binding_for,
                    class_fields,
                )
            ),
        )
    if isinstance(pattern, ast.MatchMapping):
        return _mapping_pattern_effect_receivers(
            pattern, subject_expression, subject, binding_for, class_fields
        )
    return (
        _class_pattern_effect_receivers(pattern, subject, binding_for, class_fields)
        if isinstance(pattern, ast.MatchClass)
        else ()
    )


def _mapping_pattern_effect_receivers(
    pattern: ast.MatchMapping,
    subject_expression: ast.expr | None,
    subject: _Binding,
    binding_for: Callable[[ast.AST | None], _Binding],
    class_fields: dict[str, dict[str, _Binding]],
) -> tuple[tuple[ast.pattern, _Binding, str], ...]:
    receivers = ((pattern, subject, "__getitem__"),)
    nested_receivers = tuple(
        receiver
        for key, nested_pattern in zip(pattern.keys, pattern.patterns, strict=True)
        for nested_subject in (
            _matching_dict_values(key, subject_expression)
            if isinstance(subject_expression, ast.Dict)
            else ()
        )
        for receiver in _pattern_effect_receivers(
            nested_pattern,
            nested_subject,
            binding_for(nested_subject),
            binding_for,
            class_fields,
        )
    )
    structural_receivers = tuple(
        receiver
        for key, nested_pattern in zip(pattern.keys, pattern.patterns, strict=True)
        for nested_subject in _matching_mapping_bindings(key, subject)
        for receiver in _pattern_effect_receivers(
            nested_pattern,
            None,
            nested_subject,
            binding_for,
            class_fields,
        )
    )
    return (*receivers, *nested_receivers, *structural_receivers)


def _class_pattern_effect_receivers(
    pattern: ast.MatchClass,
    subject: _Binding,
    binding_for: Callable[[ast.AST | None], _Binding],
    class_fields: dict[str, dict[str, _Binding]],
) -> tuple[tuple[ast.pattern, _Binding, str], ...]:
    class_binding = binding_for(pattern.cls)
    matched = _merge_bindings(
        subject,
        _Binding(types=class_binding.types | (class_binding.names & _TRACKED_TYPES)),
    )
    fields = _fields_for_types((matched,), class_fields)
    match_args = fields.get("__match_args__", _Binding()).string_values
    positional = tuple(
        receiver
        for index, nested in enumerate(pattern.patterns)
        for receiver in _pattern_effect_receivers(
            nested,
            None,
            fields.get(match_args[index], matched) if index < len(match_args) else matched,
            binding_for,
            class_fields,
        )
    )
    keyword = tuple(
        receiver
        for attribute, nested in zip(pattern.kwd_attrs, pattern.kwd_patterns, strict=True)
        for receiver in _pattern_effect_receivers(
            nested,
            None,
            fields.get(attribute, matched),
            binding_for,
            class_fields,
        )
    )
    return (*positional, *keyword)


def _matching_dict_values(pattern_key: ast.expr, subject: ast.Dict) -> tuple[ast.expr, ...]:
    pattern_shape = ast.dump(pattern_key, include_attributes=False)
    return tuple(
        value
        for key, value in zip(subject.keys, subject.values, strict=True)
        if key is not None
        and (
            ast.dump(key, include_attributes=False) == pattern_shape
            or _literal_values_equal(pattern_key, key)
        )
    )


def _literal_values_equal(left: ast.expr, right: ast.expr) -> bool:
    left_value = _literal_value(left)
    right_value = _literal_value(right)
    return bool(left_value and right_value and left_value[0] == right_value[0])


def _literal_value(expression: ast.expr) -> tuple[object, ...]:
    try:
        return (ast.literal_eval(expression),)
    except (TypeError, ValueError):
        return ()


def _matching_mapping_bindings(
    pattern_key: ast.expr,
    subject: _Binding,
) -> tuple[_Binding, ...]:
    pattern_value = _literal_value(pattern_key)
    if not pattern_value:
        return ()
    return tuple(
        binding
        for shape in subject.mapping_shapes
        for key, binding in shape
        if key == pattern_value[0]
    )


def _module_annotation_bindings(
    statement: ast.AnnAssign | ast.TypeAlias,
    bindings: dict[str, _Binding],
    exports: dict[str, dict[str, _Binding]],
    class_fields: dict[str, dict[str, _Binding]],
) -> dict[str, _Binding]:
    if isinstance(statement, ast.TypeAlias):
        return {
            statement.name.id: _merge_bindings(
                _annotation_binding(statement.value, bindings, exports),
                _static_expression_binding_with_exports(
                    statement.value, bindings, exports, class_fields
                ),
            )
        }
    if not isinstance(statement.target, ast.Name):
        return {}
    if _is_type_alias_annotation(statement.annotation, bindings):
        binding = _merge_bindings(
            _annotation_binding(statement.value, bindings, exports),
            _static_expression_binding_with_exports(
                statement.value, bindings, exports, class_fields
            ),
        )
    else:
        binding = _merge_bindings(
            _annotation_binding(statement.annotation, bindings, exports),
            _static_expression_binding_with_exports(
                statement.value, bindings, exports, class_fields
            ),
        )
    return {statement.target.id: binding}


def _static_expression_binding(  # noqa: PLR0911
    expression: ast.AST | None,
    bindings: dict[str, _Binding],
) -> _Binding:
    if expression is None:
        return _Binding()
    if isinstance(expression, ast.Constant):
        return _Binding(
            string_values=(expression.value,) if isinstance(expression.value, str) else (),
            can_be_none=expression.value is None,
        )
    if isinstance(expression, ast.Tuple | ast.List | ast.Set):
        return _merge_bindings(
            _literal_string_sequence_binding(expression) or _Binding(),
            _sequence_shape_binding(
                expression,
                partial(_static_expression_binding, bindings=bindings),
            ),
        )
    if isinstance(expression, ast.Dict):
        return _mapping_shape_binding(
            expression,
            partial(_static_expression_binding, bindings=bindings),
        )
    if isinstance(expression, ast.Name):
        return bindings.get(expression.id, _Binding(names=frozenset({expression.id})))
    if isinstance(expression, ast.Attribute):
        owner = _static_expression_binding(expression.value, bindings)
        names = frozenset(f"{name}.{expression.attr}" for name in owner.names)
        types = (
            _PATH_TYPES
            if owner.types & _PATH_TYPES and expression.attr == "parent"
            else frozenset()
        )
        return _merge_bindings(
            _Binding(names=names, types=types),
            _datetime_constant_binding(owner, expression.attr),
        )
    if isinstance(expression, ast.Call):
        callable_binding = _static_expression_binding(expression.func, bindings)
        return _call_result_binding(expression, callable_binding, bindings)
    if isinstance(expression, ast.NamedExpr):
        return _static_expression_binding(expression.value, bindings)
    if isinstance(expression, ast.IfExp | ast.BoolOp):
        values: Iterable[ast.expr]
        if isinstance(expression, ast.IfExp):
            values = (expression.body, expression.orelse)
        else:
            values = expression.values
        return _merge_bindings(*(_static_expression_binding(value, bindings) for value in values))
    if isinstance(expression, ast.BinOp):
        return _binary_operation_binding(
            expression.op,
            _static_expression_binding(expression.left, bindings),
            _static_expression_binding(expression.right, bindings),
        )
    return _Binding()


def _literal_string_sequence_binding(expression: ast.AST) -> _Binding | None:
    if not isinstance(expression, ast.Tuple | ast.List) or not all(
        isinstance(element, ast.Constant) and isinstance(element.value, str)
        for element in expression.elts
    ):
        return None
    return _Binding(
        string_values=tuple(
            element.value
            for element in expression.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        )
    )


def _sequence_shape_binding(
    expression: ast.Tuple | ast.List | ast.Set,
    binding_for: Callable[[ast.AST], _Binding],
) -> _Binding:
    return _Binding(sequence_shapes=(tuple(binding_for(element) for element in expression.elts),))


def _mapping_shape_binding(
    expression: ast.Dict,
    binding_for: Callable[[ast.AST], _Binding],
) -> _Binding:
    entries: list[tuple[object, _Binding]] = []
    for key, value in zip(expression.keys, expression.values, strict=True):
        if key is None or not (literal := _literal_value(key)):
            continue
        entries.append((literal[0], binding_for(value)))
    return _Binding(mapping_shapes=(tuple(entries),))


def _static_expression_binding_with_exports(  # noqa: PLR0911
    expression: ast.AST | None,
    bindings: dict[str, _Binding],
    exports: dict[str, dict[str, _Binding]],
    class_fields: dict[str, dict[str, _Binding]] | None = None,
) -> _Binding:
    result = _static_expression_binding(expression, bindings)
    if isinstance(expression, ast.Tuple | ast.List | ast.Set):
        return _merge_bindings(
            result,
            _sequence_shape_binding(
                expression,
                partial(
                    _static_expression_binding_with_exports,
                    bindings=bindings,
                    exports=exports,
                    class_fields=class_fields,
                ),
            ),
        )
    if isinstance(expression, ast.Dict):
        return _merge_bindings(
            result,
            _mapping_shape_binding(
                expression,
                partial(
                    _static_expression_binding_with_exports,
                    bindings=bindings,
                    exports=exports,
                    class_fields=class_fields,
                ),
            ),
        )
    if isinstance(expression, ast.Attribute):
        owner = _static_expression_binding_with_exports(
            expression.value, bindings, exports, class_fields
        )
        exported = tuple(
            binding
            for module_name in owner.names
            if (binding := exports.get(module_name, {}).get(expression.attr)) is not None
        )
        typed_fields = tuple(
            field
            for owner_type in owner.types
            if class_fields is not None
            and (field := class_fields.get(owner_type, {}).get(expression.attr)) is not None
        )
        return _merge_bindings(
            result,
            _typed_attribute_binding(owner, expression.attr),
            *exported,
            *typed_fields,
        )
    if isinstance(expression, ast.Call):
        binding_for = partial(
            _static_expression_binding_with_exports,
            bindings=bindings,
            exports=exports,
            class_fields=class_fields,
        )
        return _merge_bindings(
            result,
            _call_result_binding(
                expression,
                binding_for(expression.func),
                bindings,
                binding_for=binding_for,
            ),
        )
    if isinstance(expression, ast.IfExp | ast.BoolOp):
        values: Iterable[ast.expr]
        if isinstance(expression, ast.IfExp):
            values = (expression.body, expression.orelse)
        else:
            values = expression.values
        return _merge_bindings(
            result,
            *(
                _static_expression_binding_with_exports(value, bindings, exports, class_fields)
                for value in values
            ),
        )
    if isinstance(expression, ast.BinOp):
        return _merge_bindings(
            result,
            _binary_operation_binding(
                expression.op,
                _static_expression_binding_with_exports(
                    expression.left, bindings, exports, class_fields
                ),
                _static_expression_binding_with_exports(
                    expression.right, bindings, exports, class_fields
                ),
            ),
        )
    if isinstance(expression, ast.Await | ast.NamedExpr):
        return _merge_bindings(
            result,
            _static_expression_binding_with_exports(
                expression.value, bindings, exports, class_fields
            ),
        )
    return result


def _binary_operation_binding(
    operator: ast.operator,
    left: _Binding,
    right: _Binding,
) -> _Binding:
    if isinstance(operator, ast.Div) and left.types & _PATH_TYPES:
        return _Binding(types=frozenset({"pathlib.Path"}))
    if isinstance(operator, ast.Add):
        if "datetime.datetime" in left.types and right.types & _TIMEDELTA_TYPES:
            return _Binding(
                types=frozenset({"datetime.datetime"}),
                can_be_naive=left.can_be_naive,
            )
        if "datetime.datetime" in right.types and left.types & _TIMEDELTA_TYPES:
            return _Binding(
                types=frozenset({"datetime.datetime"}),
                can_be_naive=right.can_be_naive,
            )
    if (
        isinstance(operator, ast.Sub)
        and "datetime.datetime" in left.types
        and right.types & _TIMEDELTA_TYPES
    ):
        return _Binding(
            types=frozenset({"datetime.datetime"}),
            can_be_naive=left.can_be_naive,
        )
    return _Binding()


def _datetime_constant_binding(owner: _Binding, attribute: str) -> _Binding:
    if owner.names & {"datetime.datetime"} and attribute in {"max", "min"}:
        return _Binding(types=frozenset({"datetime.datetime"}), can_be_naive=True)
    if (owner.names & {"datetime.datetime"} and attribute == "resolution") or (
        owner.names & _TIMEDELTA_TYPES and attribute in {"max", "min", "resolution"}
    ):
        return _Binding(types=_TIMEDELTA_TYPES)
    return _Binding()


def _typed_attribute_binding(owner: _Binding, attribute: str) -> _Binding:
    names: set[str] = set()
    types: frozenset[str] = frozenset()
    if owner.types & _PATH_TYPES:
        names.add(f"pathlib.Path.{attribute}")
        if attribute in _PATH_RETURNING_PROPERTIES:
            types = frozenset({"pathlib.Path"})
    if owner.types & _CONFIG_PARSER_TYPES:
        names.update(
            f"{parser_type}.{attribute}" for parser_type in owner.types & _CONFIG_PARSER_TYPES
        )
    names.update(
        f"{receiver_type}.{attribute}" for receiver_type in owner.types & _EFFECT_RECEIVER_TYPES
    )
    if "datetime.datetime" in owner.types:
        names.add(f"datetime.datetime.{attribute}")
    if "datetime.date" in owner.types:
        names.add(f"datetime.date.{attribute}")
    if owner.types & _EVENT_LOOP_TYPES:
        names.add(f"asyncio.AbstractEventLoop.{attribute}")
    if owner.types & _EVENT_LOOP_POLICY_TYPES:
        names.add(f"asyncio.AbstractEventLoopPolicy.{attribute}")
    if owner.types & _RUNNER_TYPES:
        names.add(f"asyncio.Runner.{attribute}")
    if "random.Random" in owner.types:
        names.add(f"random.Random.{attribute}")
    return _Binding(
        names=frozenset(names),
        types=types,
        method_receiver_types=owner.types,
        method_receiver_can_be_naive=owner.can_be_naive,
    )


def _call_result_binding(  # noqa: PLR0912
    node: ast.Call,
    callable_binding: _Binding,
    bindings: dict[str, _Binding],
    *,
    binding_for: Callable[[ast.AST | None], _Binding] | None = None,
) -> _Binding:
    resolve = binding_for or partial(_static_expression_binding, bindings=bindings)
    result = callable_binding.returns or _Binding()
    result = _merge_bindings(
        result,
        _class_call_result_binding(node, callable_binding, resolve),
    )
    if callable_binding.names & _PATH_TYPES:
        result = _merge_bindings(result, _Binding(types=frozenset({"pathlib.Path"})))
    if callable_binding.names & _CONFIG_PARSER_TYPES:
        result = _merge_bindings(
            result,
            _Binding(types=callable_binding.names & _CONFIG_PARSER_TYPES),
        )
    if callable_binding.names & _EFFECT_RECEIVER_TYPES:
        result = _merge_bindings(
            result,
            _Binding(types=callable_binding.names & _EFFECT_RECEIVER_TYPES),
        )
    if callable_binding.names & _TIMEDELTA_TYPES:
        result = _merge_bindings(result, _Binding(types=_TIMEDELTA_TYPES))
    if callable_binding.names & {"random.Random"} or callable_binding.requires_seed:
        result = _merge_bindings(result, _Binding(types=frozenset({"random.Random"})))
    if callable_binding.names & _DATETIME_RETURNING_CALLS:
        result = _merge_bindings(
            result,
            _Binding(
                types=frozenset({"datetime.datetime"}),
                can_be_naive=_datetime_call_can_be_naive(node, callable_binding, resolve),
            ),
        )
    if callable_binding.names & (_EVENT_LOOP_RETURNING_CALLS | _EVENT_LOOP_TYPES):
        result = _merge_bindings(result, _Binding(types=frozenset({"asyncio.AbstractEventLoop"})))
    if callable_binding.names & (_EVENT_LOOP_POLICY_RETURNING_CALLS | _EVENT_LOOP_POLICY_TYPES):
        result = _merge_bindings(
            result, _Binding(types=frozenset({"asyncio.AbstractEventLoopPolicy"}))
        )
    if callable_binding.names & _RUNNER_TYPES:
        result = _merge_bindings(result, _Binding(types=frozenset({"asyncio.Runner"})))
    if isinstance(node.func, ast.Attribute):
        owner = resolve(node.func.value)
        if owner.types & _PATH_TYPES and node.func.attr in _PATH_RETURNING_METHODS:
            result = _merge_bindings(result, _Binding(types=frozenset({"pathlib.Path"})))
        if "datetime.datetime" in owner.types and node.func.attr == "astimezone":
            result = _merge_bindings(result, _Binding(types=frozenset({"datetime.datetime"})))
        if "datetime.datetime" in owner.types and node.func.attr == "replace":
            timezone = _call_argument(node, 7, frozenset({"tzinfo"}))
            can_be_naive = owner.can_be_naive if timezone is None else resolve(timezone).can_be_none
            result = _merge_bindings(
                result,
                _Binding(
                    types=frozenset({"datetime.datetime"}),
                    can_be_naive=can_be_naive,
                ),
            )
    return result


def _class_call_result_binding(
    node: ast.Call,
    callable_binding: _Binding,
    binding_for: Callable[[ast.AST | None], _Binding],
) -> _Binding:
    if not callable_binding.names & callable_binding.types:
        return _Binding()
    result = _Binding(types=callable_binding.types)
    if "datetime.datetime" not in callable_binding.types:
        return result
    return _merge_bindings(
        result,
        _Binding(
            types=frozenset({"datetime.datetime"}),
            can_be_naive=_datetime_constructor_can_be_naive(node, binding_for),
        ),
    )


def _datetime_call_can_be_naive(
    node: ast.Call,
    callable_binding: _Binding,
    binding_for: Callable[[ast.AST | None], _Binding],
) -> bool:
    if callable_binding.names & {"datetime.datetime"}:
        timezone = _call_argument(node, 7, frozenset({"tzinfo"}))
        return timezone is None or binding_for(timezone).can_be_none
    if callable_binding.names & {"datetime.datetime.fromtimestamp"}:
        timezone = _call_argument(node, 1, frozenset({"tz"}))
        return timezone is None or binding_for(timezone).can_be_none
    if callable_binding.names & {"datetime.datetime.combine"}:
        timezone = _call_argument(node, 2, frozenset({"tzinfo"}))
        return timezone is None or binding_for(timezone).can_be_none
    return bool(
        callable_binding.names
        & {
            "datetime.datetime.fromisoformat",
            "datetime.datetime.fromisocalendar",
            "datetime.datetime.fromordinal",
            "datetime.datetime.strptime",
            "datetime.datetime.utcfromtimestamp",
        }
    )


def _datetime_constructor_can_be_naive(
    node: ast.Call,
    binding_for: Callable[[ast.AST | None], _Binding],
) -> bool:
    timezone = _call_argument(node, 7, frozenset({"tzinfo"}))
    return timezone is None or binding_for(timezone).can_be_none


def _annotation_binding(  # noqa: PLR0911
    annotation: ast.AST | None,
    bindings: dict[str, _Binding],
    exports: dict[str, dict[str, _Binding]],
) -> _Binding:
    if annotation is None:
        return _Binding()
    if isinstance(annotation, ast.Constant):
        if annotation.value is None:
            return _Binding(can_be_none=True)
        if isinstance(annotation.value, str):
            try:
                parsed = ast.parse(annotation.value, mode="eval").body
            except SyntaxError:
                return _Binding()
            return _annotation_binding(parsed, bindings, exports)
        return _Binding()
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _merge_bindings(
            _annotation_binding(annotation.left, bindings, exports),
            _annotation_binding(annotation.right, bindings, exports),
        )
    if isinstance(annotation, ast.Subscript):
        owner = _static_expression_binding_with_exports(annotation.value, bindings, exports)
        items = (
            annotation.slice.elts
            if isinstance(annotation.slice, ast.Tuple)
            else (annotation.slice,)
        )
        if owner.names & (_ANNOTATED_ANNOTATIONS | _CLASSVAR_ANNOTATIONS):
            return _annotation_binding(items[0], bindings, exports)
        if owner.names & _OPTIONAL_ANNOTATIONS:
            return replace(_annotation_binding(items[0], bindings, exports), can_be_none=True)
        if owner.names & _UNION_ANNOTATIONS:
            return _merge_bindings(
                *(_annotation_binding(item, bindings, exports) for item in items)
            )
        types = owner.types | (owner.names & _TRACKED_TYPES)
        if types:
            return _Binding(
                types=types,
                can_be_none=owner.can_be_none,
                can_be_naive=owner.can_be_naive or "datetime.datetime" in types,
            )
        return _Binding()
    resolved = _static_expression_binding_with_exports(annotation, bindings, exports)
    types = resolved.types | (resolved.names & _TRACKED_TYPES)
    return _Binding(
        types=types,
        can_be_none=resolved.can_be_none,
        can_be_naive=resolved.can_be_naive or "datetime.datetime" in types,
    )


def _is_type_alias_annotation(
    annotation: ast.AST | None,
    bindings: dict[str, _Binding],
) -> bool:
    return bool(_static_expression_binding(annotation, bindings).names & _TYPE_ALIAS_ANNOTATIONS)


class _CapabilityVisitor(ast.NodeVisitor):
    def __init__(
        self,
        source: _ParsedSource,
        exports: dict[str, dict[str, _Binding]],
        *,
        class_fields: dict[str, dict[str, _Binding]],
    ) -> None:
        self._source = source
        self._exports = exports
        self._known_class_fields = {
            type_name: dict(fields) for type_name, fields in class_fields.items()
        }
        self._scopes: list[dict[str, _Binding]] = [dict(exports.get(source.module_name, {}))]
        self._class_fields: list[dict[str, _Binding]] = []
        self._class_names: list[str] = []
        self._function_depth = 0
        self._comprehension_binding_scopes: list[dict[str, _Binding]] = []
        self._violations: set[Violation] = set()

    def inspect(self) -> tuple[Violation, ...]:
        self.visit(self._source.tree)
        return tuple(sorted(self._violations))

    def _add_effect_receiver_protocol(
        self,
        node: ast.AST,
        binding: _Binding,
        protocol: str,
    ) -> None:
        for prohibition in _effect_receiver_protocol_prohibitions(binding, protocol):
            self._add(node, *prohibition)

    def _visit_truth_test(self, expression: ast.expr) -> None:
        self._add_truth_test(expression)
        self.visit(expression)

    def _add_truth_test(self, expression: ast.expr) -> None:
        if isinstance(expression, ast.BoolOp):
            for value in expression.values:
                self._add_truth_test(value)
            return
        if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
            self._add_truth_test(expression.operand)
            return
        self._add_effect_receiver_protocol(expression, self._binding_for(expression), "__bool__")

    @override
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            prohibition = _forbidden_import(alias.name)
            if prohibition is not None:
                self._add(alias, *prohibition)
            bound_name = alias.asname or alias.name.split(".")[0]
            qualified_name = alias.name if alias.asname else alias.name.split(".")[0]
            self._bind(bound_name, _Binding(names=frozenset({qualified_name})))

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = _resolve_import_module(self._source, module=node.module, level=node.level)
        prohibition = _forbidden_import(module)
        if prohibition is not None:
            self._add(node, *prohibition)
        if len(node.names) == 1 and node.names[0].name == "*":
            wildcard = _wildcard_prohibition(module)
            if wildcard is not None:
                self._add(node.names[0], *wildcard)
            for name, binding in self._exports.get(module, {}).items():
                self._bind(name, binding)
            return
        for alias in node.names:
            qualified_name = f"{module}.{alias.name}"
            child_prohibition = _forbidden_import(qualified_name)
            reference = _prohibition_for_reference(qualified_name)
            if child_prohibition is not None:
                self._add(alias, *child_prohibition)
            elif prohibition is None and reference is not None:
                self._add(alias, reference[0], "effect dependency", qualified_name)
            binding = self._exports.get(module, {}).get(alias.name) or _Binding(
                names=frozenset({qualified_name})
            )
            self._bind(alias.asname or alias.name, binding)

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        return_binding = _annotation_binding(node.returns, self._visible_bindings(), self._exports)
        self._bind(
            node.name,
            _Binding(
                names=frozenset({f"{self._source.module_name}.{node.name}"}),
                returns=return_binding,
            ),
        )
        local_scope: dict[str, _Binding] = {}
        arguments = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        for argument in arguments:
            local_scope[argument.arg] = _annotation_binding(
                argument.annotation, self._visible_bindings(), self._exports
            )
        if node.args.vararg is not None:
            local_scope[node.args.vararg.arg] = _Binding()
        if node.args.kwarg is not None:
            local_scope[node.args.kwarg.arg] = _Binding()
        self._scopes.append(local_scope)
        self._function_depth += 1
        for statement in node.body:
            self.visit(statement)
        self._function_depth -= 1
        self._scopes.pop()

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        if self._function_depth:
            qualified_name = f"{self._source.module_name}.{node.name}@{node.lineno}"
        elif self._class_names:
            qualified_name = f"{self._class_names[-1]}.{node.name}"
        else:
            qualified_name = f"{self._source.module_name}.{node.name}"
        base_bindings = tuple(self._binding_for(base) for base in node.bases)
        class_binding = _class_definition_binding(qualified_name, base_bindings)
        self._bind(node.name, class_binding)
        fields = _merge_field_maps(
            _fields_for_types(base_bindings, self._known_class_fields),
            _class_field_bindings(
                self._source,
                node,
                qualified_name,
                self._visible_bindings(),
                self._exports,
                self._known_class_fields,
            ),
        )
        self._known_class_fields[qualified_name] = fields
        self._class_fields.append(fields)
        self._class_names.append(qualified_name)
        self._scopes.append({})
        for statement in node.body:
            self.visit(statement)
        self._scopes.pop()
        self._class_names.pop()
        self._class_fields.pop()

    @override
    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            for value in _unpacking_protocol_values(target, node.value):
                self._add_effect_receiver_protocol(value, self._binding_for(value), "__iter__")
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)
            self._bind_assignment(target, node.value)

    @override
    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and _is_type_alias_annotation(
            node.annotation, self._visible_bindings()
        ):
            self._bind(
                node.target.id,
                _merge_bindings(
                    _annotation_binding(node.value, self._visible_bindings(), self._exports),
                    self._binding_for(node.value),
                ),
            )
            return
        if node.value is not None:
            self.visit(node.value)
        self.visit(node.target)
        binding = _merge_bindings(
            _annotation_binding(node.annotation, self._visible_bindings(), self._exports),
            self._binding_for(node.value),
        )
        self._bind_target(node.target, binding)

    @override
    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:
        self._bind(
            node.name.id,
            _merge_bindings(
                _annotation_binding(node.value, self._visible_bindings(), self._exports),
                self._binding_for(node.value),
            ),
        )

    @override
    def visit_If(self, node: ast.If) -> None:
        self._visit_truth_test(node.test)
        original = dict(self._scopes[-1])
        none_guard = _none_guard(node.test)
        awareness_guard = _datetime_awareness_guard(node.test)
        branches = (
            (
                self._visit_branch(
                    _narrowed_scope(
                        original,
                        none_guard,
                        awareness_guard,
                        when_true=True,
                    ),
                    node.body,
                ),
                node.body,
            ),
            (
                self._visit_branch(
                    _narrowed_scope(
                        original,
                        none_guard,
                        awareness_guard,
                        when_true=False,
                    ),
                    node.orelse,
                ),
                node.orelse,
            ),
        )
        continuing = tuple(
            scope for scope, statements in branches if _block_falls_through(statements)
        )
        self._scopes[-1] = _join_scopes(*(continuing or (original,)))

    @override
    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        subject = self._binding_for(node.subject)
        original = dict(self._scopes[-1])
        branches: list[dict[str, _Binding]] = [original]
        next_case_input = dict(original)
        for case in node.cases:
            self._scopes[-1] = dict(next_case_input)
            for pattern, receiver, protocol in _pattern_effect_receivers(
                case.pattern,
                node.subject,
                subject,
                self._binding_for,
                self._known_class_fields,
            ):
                self._add_effect_receiver_protocol(pattern, receiver, protocol)
            self.visit(case.pattern)
            self._scopes[-1].update(
                _pattern_capture_bindings(
                    case.pattern,
                    node.subject,
                    subject,
                    self._binding_for,
                    self._known_class_fields,
                )
            )
            if case.guard is not None:
                self._visit_truth_test(case.guard)
            next_case_input = _join_scopes(next_case_input, self._scopes[-1])
            case_scope = self._visit_branch(dict(self._scopes[-1]), case.body)
            if _block_falls_through(case.body):
                branches.append(case_scope)
        self._scopes[-1] = _join_scopes(*branches, next_case_input)

    @override
    def visit_Try(self, node: ast.Try) -> None:
        self._visit_try(node)

    @override
    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_try(node)

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        original = dict(self._scopes[-1])
        body_prefixes = _possible_prefix_scopes(
            self._source,
            node.body,
            self._exports,
            self._known_class_fields,
            self._visible_bindings(),
        )
        body_scope = self._visit_branch(original, node.body)
        success_scope = self._visit_branch(body_scope, node.orelse)
        continuing = [success_scope] if _block_falls_through(node.body) else []
        handler_outcomes: list[dict[str, _Binding]] = []
        possible_handler_input = _join_scopes(*body_prefixes)
        for handler in node.handlers:
            self._scopes[-1] = dict(possible_handler_input)
            if handler.type is not None:
                self.visit(handler.type)
            if handler.name is not None:
                self._bind(handler.name, _Binding())
            handler_scope = self._visit_branch(dict(self._scopes[-1]), handler.body)
            handler_outcomes.append(handler_scope)
            if _block_falls_through(handler.body):
                continuing.append(handler_scope)
        all_final_inputs = _join_scopes(
            *body_prefixes,
            success_scope,
            *handler_outcomes,
        )
        self._visit_branch(all_final_inputs, node.finalbody)
        continuing_input = _join_scopes(*(continuing or [original]))
        self._scopes[-1] = self._visit_branch(continuing_input, node.finalbody)

    @override
    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    @override
    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    @override
    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    @override
    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            protocol = "__aenter__" if isinstance(node, ast.AsyncWith) else "__enter__"
            self._add_effect_receiver_protocol(
                item.context_expr, self._binding_for(item.context_expr), protocol
            )
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self.visit(item.optional_vars)
                self._bind_target(item.optional_vars, self._binding_for(item.context_expr))
        self._visit_branch(dict(self._scopes[-1]), node.body)

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        protocol = "__aiter__" if isinstance(node, ast.AsyncFor) else "__iter__"
        self._add_effect_receiver_protocol(node.iter, self._binding_for(node.iter), protocol)
        for iterated_value in _iterated_value_expressions(node.iter):
            for value in _unpacking_protocol_values(node.target, iterated_value):
                self._add_effect_receiver_protocol(value, self._binding_for(value), "__iter__")
        self.visit(node.iter)
        self.visit(node.target)
        original = dict(self._scopes[-1])
        repeated = dict(original)
        break_scopes: list[dict[str, _Binding]] = []
        for _ in range(max(1, len(node.body) + len(original) + 1)):
            self._scopes[-1] = dict(repeated)
            for target, binding in _iterated_target_bindings(
                node.target, node.iter, self._binding_for
            ):
                self._bind_target(target, binding)
            body_input = dict(self._scopes[-1])
            jumps = _collect_block_jumps(
                self._source,
                node.body,
                self._exports,
                self._known_class_fields,
                self._visible_bindings(),
            )
            break_scopes.extend(jumps.breaks)
            self._visit_branch(body_input, node.body)
            back_edges = (
                *((jumps.fallthrough,) if jumps.fallthrough is not None else ()),
                *jumps.continues,
            )
            updated = _join_scopes(original, *back_edges)
            if updated == repeated:
                break
            repeated = updated
        after_else = self._visit_branch(repeated, node.orelse)
        self._scopes[-1] = _join_scopes(after_else, *break_scopes)

    @override
    def visit_While(self, node: ast.While) -> None:
        original = dict(self._scopes[-1])
        repeated = dict(original)
        test_scope = dict(original)
        break_scopes: list[dict[str, _Binding]] = []
        for _ in range(max(1, len(node.body) + len(original) + 1)):
            self._scopes[-1] = dict(repeated)
            self._visit_truth_test(node.test)
            body_input = dict(self._scopes[-1])
            test_scope = _join_scopes(test_scope, body_input)
            jumps = _collect_block_jumps(
                self._source,
                node.body,
                self._exports,
                self._known_class_fields,
                self._visible_bindings(),
            )
            break_scopes.extend(jumps.breaks)
            self._visit_branch(body_input, node.body)
            back_edges = (
                *((jumps.fallthrough,) if jumps.fallthrough is not None else ()),
                *jumps.continues,
            )
            updated = _join_scopes(original, *back_edges)
            if updated == repeated:
                break
            repeated = updated
        after_else = self._visit_branch(_join_scopes(repeated, test_scope), node.orelse)
        self._scopes[-1] = _join_scopes(after_else, *break_scopes)

    def _visit_branch(
        self,
        initial: dict[str, _Binding],
        statements: list[ast.stmt],
    ) -> dict[str, _Binding]:
        self._scopes[-1] = dict(initial)
        for statement in statements:
            self.visit(statement)
            if not _block_falls_through([statement]):
                break
        return dict(self._scopes[-1])

    @override
    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        binding = self._binding_for(node.value)
        if self._comprehension_binding_scopes and isinstance(node.target, ast.Name):
            self._comprehension_binding_scopes[-1][node.target.id] = binding
        else:
            self._bind_target(node.target, binding)

    @override
    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._visit_truth_test(node.test)
        self.visit(node.body)
        self.visit(node.orelse)

    @override
    def visit_Assert(self, node: ast.Assert) -> None:
        self._visit_truth_test(node.test)
        if node.msg is not None:
            self.visit(node.msg)

    @override
    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        for value in node.values[:-1]:
            self._visit_truth_test(value)
        self.visit(node.values[-1])

    @override
    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        if isinstance(node.op, ast.Not):
            self._visit_truth_test(node.operand)
        else:
            self.visit(node.operand)

    def _add_truth_consumer_protocols(
        self,
        node: ast.Call,
        callable_binding: _Binding,
    ) -> None:
        iterable: ast.expr | None = None
        if callable_binding.names & {"all", "any", "builtins.all", "builtins.any"}:
            iterable = _call_argument(node, 0, frozenset())
        elif callable_binding.names & {"builtins.filter", "filter"}:
            predicate = _call_argument(node, 0, frozenset())
            if predicate is not None:
                predicate_binding = self._binding_for(predicate)
                uses_truth = (
                    _expression_can_be_none(predicate)
                    or predicate_binding.can_be_none
                    or bool(predicate_binding.names & {"bool", "builtins.bool", "operator.truth"})
                )
                if uses_truth:
                    iterable = _call_argument(node, 1, frozenset())
        if iterable is None:
            return
        if isinstance(iterable, ast.Tuple | ast.List | ast.Set):
            for element in iterable.elts:
                self._add_truth_test(element)
            return
        if isinstance(iterable, ast.Dict):
            for key in iterable.keys:
                if key is not None:
                    self._add_truth_test(key)
            return
        binding = self._binding_for(iterable)
        for shape in binding.sequence_shapes:
            for element_binding in shape:
                self._add_effect_receiver_protocol(node, element_binding, "__bool__")

    @override
    def visit_Call(self, node: ast.Call) -> None:
        callable_binding = self._binding_for(node.func)
        receiver = (
            self._binding_for(node.func.value) if isinstance(node.func, ast.Attribute) else None
        )
        for prohibition in _prohibited_calls(node, callable_binding, receiver, self._binding_for):
            self._add(node, *prohibition)
        protocol = _builtin_protocol(callable_binding.names)
        if protocol is not None and node.args:
            self._add_effect_receiver_protocol(node, self._binding_for(node.args[0]), protocol)
        iteration_positions = {
            position
            for name in callable_binding.names
            for position in _ITERATION_BUILTIN_ARGUMENTS.get(name, ())
        }
        if callable_binding.names & {"builtins.map", "map"}:
            iteration_positions.update(range(1, len(node.args)))
        if callable_binding.names & {"builtins.zip", "zip"}:
            iteration_positions.update(range(len(node.args)))
        for position in sorted(iteration_positions):
            if position < len(node.args):
                self._add_effect_receiver_protocol(
                    node, self._binding_for(node.args[position]), "__iter__"
                )
        self._add_truth_consumer_protocols(node, callable_binding)

        environment_call = any(
            _environment_object_for_call(name) is not None for name in callable_binding.names
        )
        if isinstance(node.func, ast.Attribute):
            if not environment_call:
                self.visit(node.func.value)
        elif not isinstance(node.func, ast.Name):
            self.visit(node.func)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            if keyword.arg is None:
                self._add_effect_receiver_protocol(
                    keyword.value, self._binding_for(keyword.value), "__iter__"
                )
            self.visit(keyword.value)

    @override
    def visit_Attribute(self, node: ast.Attribute) -> None:
        binding = self._binding_for(node)
        for name in binding.names:
            prohibition = _forbidden_import(name) or _prohibition_for_reference(name)
            if prohibition is not None:
                self._add(node, *prohibition)
        self.visit(node.value)

    @override
    def visit_Subscript(self, node: ast.Subscript) -> None:
        binding = self._binding_for(node.value)
        environment = sorted(binding.names & _ENVIRONMENT_OBJECTS)
        if environment:
            self._add(node, "CAP004", "environment access", environment[0])
        protocol = (
            "__setitem__"
            if isinstance(node.ctx, ast.Store)
            else "__delitem__"
            if isinstance(node.ctx, ast.Del)
            else "__getitem__"
        )
        self._add_effect_receiver_protocol(node, binding, protocol)
        self.visit(node.value)
        self.visit(node.slice)

    @override
    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    @override
    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    @override
    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    @override
    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        results: tuple[ast.expr, ...],
    ) -> None:
        containing_scope = (
            self._comprehension_binding_scopes[-1]
            if self._comprehension_binding_scopes
            else self._scopes[-1]
        )
        self._comprehension_binding_scopes.append(containing_scope)
        self._scopes.append({})
        try:
            limit = max(
                1,
                len(containing_scope)
                + len(generators)
                + sum(len(generator.ifs) for generator in generators)
                + len(results)
                + 1,
            )
            for _ in range(limit):
                before = dict(containing_scope)
                self._scopes[-1] = {}
                for generator in generators:
                    protocol = "__aiter__" if generator.is_async else "__iter__"
                    self._add_effect_receiver_protocol(
                        generator.iter, self._binding_for(generator.iter), protocol
                    )
                    for iterated_value in _iterated_value_expressions(generator.iter):
                        for value in _unpacking_protocol_values(generator.target, iterated_value):
                            self._add_effect_receiver_protocol(
                                value, self._binding_for(value), "__iter__"
                            )
                    self.visit(generator.iter)
                    self.visit(generator.target)
                    for target, binding in _iterated_target_bindings(
                        generator.target, generator.iter, self._binding_for
                    ):
                        self._bind_target(target, binding)
                    for condition in generator.ifs:
                        self._visit_truth_test(condition)
                for result in results:
                    self.visit(result)
                if containing_scope == before:
                    break
        finally:
            self._scopes.pop()
            self._comprehension_binding_scopes.pop()

    @override
    def visit_Starred(self, node: ast.Starred) -> None:
        if isinstance(node.ctx, ast.Load):
            self._add_effect_receiver_protocol(
                node.value, self._binding_for(node.value), "__iter__"
            )
        self.visit(node.value)

    @override
    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        self._add_effect_receiver_protocol(node.value, self._binding_for(node.value), "__iter__")
        self.visit(node.value)

    @override
    def visit_Compare(self, node: ast.Compare) -> None:
        self.visit(node.left)
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            if isinstance(operator, ast.In | ast.NotIn):
                self._add_effect_receiver_protocol(
                    comparator, self._binding_for(comparator), "__contains__"
                )
            self.visit(comparator)

    @override
    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                self._add_effect_receiver_protocol(value, self._binding_for(value), "__iter__")
            else:
                self.visit(key)
            self.visit(value)

    def _binding_for(  # noqa: PLR0911, PLR0912
        self, expression: ast.AST | None
    ) -> _Binding:
        if expression is None:
            return _Binding()
        if isinstance(expression, ast.Constant):
            return _Binding(
                string_values=(expression.value,) if isinstance(expression.value, str) else (),
                can_be_none=expression.value is None,
            )
        if isinstance(expression, ast.Tuple | ast.List | ast.Set):
            return _merge_bindings(
                _literal_string_sequence_binding(expression) or _Binding(),
                _sequence_shape_binding(expression, self._binding_for),
            )
        if isinstance(expression, ast.Dict):
            return _mapping_shape_binding(expression, self._binding_for)
        literal_strings = _literal_string_sequence_binding(expression)
        if literal_strings is not None:
            return literal_strings
        if isinstance(expression, ast.Name):
            found = self._lookup(expression.id)
            if found is not None:
                return found
            builtin_name = "builtins.open" if expression.id == "open" else expression.id
            return _Binding(names=frozenset({builtin_name}))
        if isinstance(expression, ast.Attribute):
            if (
                isinstance(expression.value, ast.Name)
                and expression.value.id in {"self", "cls"}
                and self._class_fields
            ):
                field = self._class_fields[-1].get(expression.attr)
                if field is not None:
                    return field
            owner = self._binding_for(expression.value)
            names = frozenset(f"{name}.{expression.attr}" for name in owner.names)
            exported = tuple(
                binding
                for module_name in owner.names
                if (binding := self._exports.get(module_name, {}).get(expression.attr)) is not None
            )
            typed_fields = tuple(
                field
                for owner_type in owner.types
                if (field := self._known_class_fields.get(owner_type, {}).get(expression.attr))
                is not None
            )
            return _merge_bindings(
                _Binding(
                    names=names,
                    method_receiver_types=owner.types,
                    method_receiver_can_be_naive=owner.can_be_naive,
                ),
                _typed_attribute_binding(owner, expression.attr),
                _datetime_constant_binding(owner, expression.attr),
                *exported,
                *typed_fields,
            )
        if isinstance(expression, ast.Call):
            return _call_result_binding(
                expression,
                self._binding_for(expression.func),
                self._visible_bindings(),
                binding_for=self._binding_for,
            )
        if isinstance(expression, ast.NamedExpr):
            return self._binding_for(expression.value)
        if isinstance(expression, ast.Await):
            return self._binding_for(expression.value)
        if isinstance(expression, ast.IfExp):
            body = self._binding_for(expression.body)
            otherwise = self._binding_for(expression.orelse)
            guarded = _none_guard(expression.test)
            if guarded is not None:
                name, non_none_when_true = guarded
                if (
                    non_none_when_true
                    and isinstance(expression.body, ast.Name)
                    and expression.body.id == name
                ):
                    body = replace(body, can_be_none=False)
                if (
                    not non_none_when_true
                    and isinstance(expression.orelse, ast.Name)
                    and expression.orelse.id == name
                ):
                    otherwise = replace(otherwise, can_be_none=False)
            return _merge_bindings(body, otherwise)
        if isinstance(expression, ast.BoolOp):
            values = tuple(self._binding_for(value) for value in expression.values)
            merged = _merge_bindings(*values)
            if isinstance(expression.op, ast.Or):
                return replace(merged, can_be_none=all(value.can_be_none for value in values))
            return merged
        if isinstance(expression, ast.BinOp):
            return _binary_operation_binding(
                expression.op,
                self._binding_for(expression.left),
                self._binding_for(expression.right),
            )
        return _Binding()

    def _bind_target(self, target: ast.AST, binding: _Binding) -> None:
        if isinstance(target, ast.Name):
            self._bind(target.id, binding)
            return
        if isinstance(target, ast.Starred):
            self._bind_target(target.value, binding)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind_target(element, binding)
            return
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id in {"self", "cls"}
            and self._class_fields
        ):
            current = self._class_fields[-1].get(target.attr, _Binding())
            self._class_fields[-1][target.attr] = _merge_bindings(current, binding)

    def _bind_assignment(self, target: ast.AST, value: ast.AST) -> None:
        for assignment_target, binding in _assignment_bindings(target, value, self._binding_for):
            self._bind_target(assignment_target, binding)

    def _bind(self, name: str, binding: _Binding) -> None:
        self._scopes[-1][name] = binding

    def _lookup(self, name: str) -> _Binding | None:
        for scope in reversed(self._scopes):
            if name in scope:
                return scope[name]
        return None

    def _visible_bindings(self) -> dict[str, _Binding]:
        visible: dict[str, _Binding] = {}
        for scope in self._scopes:
            visible.update(scope)
        return visible

    def _add(self, node: ast.AST, rule_id: str, kind: str, subject: str) -> None:
        self._violations.add(
            Violation(
                path=self._source.relative_path,
                line=getattr(node, "lineno", 1),
                column=getattr(node, "col_offset", 0) + 1,
                rule_id=rule_id,
                kind=kind,
                subject=subject,
            )
        )


def _receiver_attribute(target: ast.AST, receiver: str) -> bool:
    return (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == receiver
    )


def _class_field_bindings(  # noqa: PLR0913, PLR0917
    source: _ParsedSource,
    node: ast.ClassDef,
    qualified_name: str,
    visible: dict[str, _Binding],
    exports: dict[str, dict[str, _Binding]],
    known_class_fields: dict[str, dict[str, _Binding]],
) -> dict[str, _Binding]:
    fields, _ = _class_body_field_bindings(
        source,
        node.body,
        qualified_name,
        visible,
        exports,
        known_class_fields,
    )
    if "__match_args__" not in fields:
        match_args = _generated_dataclass_match_args(
            node,
            visible,
            exports,
            known_class_fields,
        )
        if match_args is not None:
            fields["__match_args__"] = _Binding(string_values=match_args)
    return fields


def _generated_dataclass_match_args(
    node: ast.ClassDef,
    visible: dict[str, _Binding],
    exports: dict[str, dict[str, _Binding]],
    known_class_fields: dict[str, dict[str, _Binding]],
) -> tuple[str, ...] | None:
    binding_for = partial(
        _static_expression_binding_with_exports,
        bindings=visible,
        exports=exports,
        class_fields=known_class_fields,
    )
    decorator = next(
        (
            candidate
            for candidate in node.decorator_list
            if binding_for(candidate.func if isinstance(candidate, ast.Call) else candidate).names
            & {"dataclasses.dataclass"}
        ),
        None,
    )
    if decorator is None:
        return None
    if isinstance(decorator, ast.Call) and not _boolean_keyword(
        decorator, "match_args", conservative_default=True
    ):
        return None
    default_kw_only = isinstance(decorator, ast.Call) and _boolean_keyword(
        decorator, "kw_only", conservative_default=False
    )
    inherited = tuple(
        field_name
        for base in node.bases
        for type_name in binding_for(base).types
        for field_name in known_class_fields.get(type_name, {})
        .get("__match_args__", _Binding())
        .string_values
    )
    positional = list(dict.fromkeys(inherited))
    kw_only = default_kw_only
    for statement in node.body:
        if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
            continue
        annotation_owner = (
            statement.annotation.value
            if isinstance(statement.annotation, ast.Subscript)
            else statement.annotation
        )
        annotation_names = binding_for(annotation_owner).names
        if annotation_names & _CLASSVAR_ANNOTATIONS:
            continue
        if annotation_names & {"dataclasses.KW_ONLY"}:
            kw_only = True
            continue
        field_call = (
            statement.value
            if isinstance(statement.value, ast.Call)
            and binding_for(statement.value.func).names & {"dataclasses.field"}
            else None
        )
        if field_call is not None and not _boolean_keyword(
            field_call, "init", conservative_default=True
        ):
            continue
        field_kw_only = (
            _boolean_keyword(field_call, "kw_only", conservative_default=kw_only)
            if field_call is not None
            else kw_only
        )
        if not field_kw_only:
            positional.append(statement.target.id)
    return tuple(positional)


def _boolean_keyword(
    call: ast.Call,
    name: str,
    *,
    conservative_default: bool,
) -> bool:
    for keyword in call.keywords:
        if (
            keyword.arg == name
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, bool)
        ):
            return keyword.value.value
    return conservative_default


def _class_body_field_bindings(  # noqa: PLR0912, PLR0913, PLR0915, PLR0917
    source: _ParsedSource,
    statements: list[ast.stmt],
    owner_type: str,
    visible: dict[str, _Binding],
    exports: dict[str, dict[str, _Binding]],
    known_class_fields: dict[str, dict[str, _Binding]],
) -> tuple[dict[str, _Binding], dict[str, _Binding]]:
    fields: dict[str, _Binding] = {}
    scope = dict(visible)
    binding_for = partial(
        _static_expression_binding_with_exports,
        bindings=scope,
        exports=exports,
        class_fields=known_class_fields,
    )
    for statement in statements:
        named = _record_named_expressions(
            _non_control_statement_expressions(statement), scope, binding_for
        )
        fields = _merge_field_maps(fields, named)
        if isinstance(statement, ast.Import | ast.ImportFrom):
            imported = _import_statement_exports(source, statement, exports)
            scope.update(imported)
            fields = _merge_field_maps(fields, imported)
            continue
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            binding = _merge_bindings(
                _annotation_binding(statement.annotation, scope, exports),
                binding_for(statement.value),
            )
            fields[statement.target.id] = _merge_bindings(
                fields.get(statement.target.id, _Binding()), binding
            )
            scope[statement.target.id] = binding
            continue
        if isinstance(statement, ast.Assign):
            for assignment_target in statement.targets:
                for field_target, binding in _assignment_bindings(
                    assignment_target, statement.value, binding_for
                ):
                    if (name := _assignment_name(field_target)) is not None:
                        fields[name] = _merge_bindings(fields.get(name, _Binding()), binding)
                        scope[name] = binding
            continue
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            fields = _merge_field_maps(
                fields,
                _method_field_bindings(
                    source,
                    statement,
                    owner_type,
                    visible,
                    exports,
                    known_class_fields,
                ),
            )
            continue
        if isinstance(statement, ast.ClassDef):
            qualified_name = f"{owner_type}.{statement.name}"
            binding = _class_definition_binding(
                qualified_name,
                (binding_for(base) for base in statement.bases),
            )
            scope[statement.name] = binding
            fields[statement.name] = _merge_bindings(
                fields.get(statement.name, _Binding()), binding
            )
            continue
        if isinstance(statement, ast.Try | ast.TryStar):
            try_fields, try_scope = _class_try_field_bindings(
                source,
                statement,
                owner_type,
                scope,
                exports,
                known_class_fields,
            )
            fields = _merge_field_maps(fields, try_fields)
            scope.clear()
            scope.update(try_scope)
            continue
        if isinstance(statement, ast.For | ast.AsyncFor | ast.While):
            loop_fields, loop_scope = _class_loop_field_bindings(
                source,
                statement,
                owner_type,
                scope,
                exports,
                known_class_fields,
            )
            fields = _merge_field_maps(fields, loop_fields)
            scope.clear()
            scope.update(loop_scope)
            continue
        before_control = dict(scope)
        branch_scopes: list[dict[str, _Binding]] = []
        for block, initial_scope in _control_flow_branches(
            statement, scope, exports, known_class_fields
        ):
            fields = _merge_field_maps(
                fields,
                _changed_scope_bindings(before_control, initial_scope),
            )
            branch_fields, branch_scope = _class_body_field_bindings(
                source,
                block,
                owner_type,
                initial_scope,
                exports,
                known_class_fields,
            )
            fields = _merge_field_maps(fields, branch_fields)
            branch_scopes.append(branch_scope)
        if branch_scopes:
            joined = _join_scopes(scope, *branch_scopes)
            scope.clear()
            scope.update(joined)
    return fields, scope


def _class_try_field_bindings(  # noqa: PLR0913, PLR0917
    source: _ParsedSource,
    statement: ast.Try | ast.TryStar,
    owner_type: str,
    initial: dict[str, _Binding],
    exports: dict[str, dict[str, _Binding]],
    known_class_fields: dict[str, dict[str, _Binding]],
) -> tuple[dict[str, _Binding], dict[str, _Binding]]:
    body_fields, body_scope = _class_body_field_bindings(
        source,
        statement.body,
        owner_type,
        initial,
        exports,
        known_class_fields,
    )
    success_fields, success_scope = _class_body_field_bindings(
        source,
        statement.orelse,
        owner_type,
        body_scope,
        exports,
        known_class_fields,
    )
    body_prefixes = _class_possible_prefix_scopes(
        source,
        statement.body,
        owner_type,
        initial,
        exports,
        known_class_fields,
    )
    possible_handler_input = _join_scopes(*body_prefixes)
    handler_results: list[tuple[dict[str, _Binding], dict[str, _Binding]]] = []
    for handler in statement.handlers:
        handler_input = dict(possible_handler_input)
        if handler.name is not None:
            handler_input[handler.name] = _Binding()
        handler_results.append(
            _class_body_field_bindings(
                source,
                handler.body,
                owner_type,
                handler_input,
                exports,
                known_class_fields,
            )
        )
    joined = _join_scopes(
        initial,
        *body_prefixes,
        success_scope,
        *(handler_scope for _, handler_scope in handler_results),
    )
    final_fields, final_scope = _class_body_field_bindings(
        source,
        statement.finalbody,
        owner_type,
        joined,
        exports,
        known_class_fields,
    )
    return (
        _merge_field_maps(
            body_fields,
            success_fields,
            *(handler_fields for handler_fields, _ in handler_results),
            final_fields,
        ),
        final_scope,
    )


def _class_possible_prefix_scopes(  # noqa: PLR0913, PLR0917
    source: _ParsedSource,
    statements: list[ast.stmt],
    owner_type: str,
    initial: dict[str, _Binding],
    exports: dict[str, dict[str, _Binding]],
    known_class_fields: dict[str, dict[str, _Binding]],
) -> tuple[dict[str, _Binding], ...]:
    current = dict(initial)
    prefixes: list[dict[str, _Binding]] = [current]
    for statement in statements:
        branch_seed = dict(current)
        branches = _control_flow_branches(
            statement,
            branch_seed,
            exports,
            known_class_fields,
        )
        for block, branch_scope in branches:
            prefixes.extend(
                _class_possible_prefix_scopes(
                    source,
                    block,
                    owner_type,
                    branch_scope,
                    exports,
                    known_class_fields,
                )
            )
        _, current = _class_body_field_bindings(
            source,
            [statement],
            owner_type,
            current,
            exports,
            known_class_fields,
        )
        prefixes.append(current)
    return tuple(prefixes)


def _class_loop_field_bindings(  # noqa: PLR0913, PLR0917
    source: _ParsedSource,
    statement: ast.For | ast.AsyncFor | ast.While,
    owner_type: str,
    initial: dict[str, _Binding],
    exports: dict[str, dict[str, _Binding]],
    known_class_fields: dict[str, dict[str, _Binding]],
) -> tuple[dict[str, _Binding], dict[str, _Binding]]:
    before_loop = dict(initial)
    binding_for = partial(
        _static_expression_binding_with_exports,
        bindings=before_loop,
        exports=exports,
        class_fields=known_class_fields,
    )
    header = statement.iter if isinstance(statement, ast.For | ast.AsyncFor) else statement.test
    named = _record_named_expressions((header,), before_loop, binding_for)
    fields = dict(named)
    repeated = dict(before_loop)
    break_scopes: list[dict[str, _Binding]] = []
    for _ in range(max(1, len(statement.body) + len(before_loop) + 1)):
        body_input = dict(repeated)
        if isinstance(statement, ast.For | ast.AsyncFor):
            body_binding_for = partial(
                _static_expression_binding_with_exports,
                bindings=body_input,
                exports=exports,
                class_fields=known_class_fields,
            )
            for target, binding in _iterated_target_bindings(
                statement.target, statement.iter, body_binding_for
            ):
                body_input.update(_scope_target_bindings(target, binding))
        fields = _merge_field_maps(fields, _changed_scope_bindings(repeated, body_input))
        body_fields, _ = _class_body_field_bindings(
            source,
            statement.body,
            owner_type,
            body_input,
            exports,
            known_class_fields,
        )
        fields = _merge_field_maps(fields, body_fields)
        jumps = _collect_block_jumps(
            source,
            statement.body,
            exports,
            known_class_fields,
            body_input,
        )
        break_scopes.extend(jumps.breaks)
        back_edges = (
            *((jumps.fallthrough,) if jumps.fallthrough is not None else ()),
            *jumps.continues,
        )
        updated = _join_scopes(before_loop, *back_edges)
        if updated == repeated:
            break
        repeated = updated
    else_fields, else_scope = _class_body_field_bindings(
        source,
        statement.orelse,
        owner_type,
        repeated,
        exports,
        known_class_fields,
    )
    return _merge_field_maps(fields, else_fields), _join_scopes(else_scope, *break_scopes)


def _method_field_bindings(  # noqa: PLR0913, PLR0917
    source: _ParsedSource,
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
    owner_type: str,
    visible: dict[str, _Binding],
    exports: dict[str, dict[str, _Binding]],
    known_class_fields: dict[str, dict[str, _Binding]],
) -> dict[str, _Binding]:
    positional = (*statement.args.posonlyargs, *statement.args.args)
    if not positional:
        return {}
    receiver = positional[0].arg
    method_visible = dict(visible)
    for argument in (*positional, *statement.args.kwonlyargs):
        method_visible[argument.arg] = _annotation_binding(argument.annotation, visible, exports)
    method_visible[receiver] = _merge_bindings(
        method_visible[receiver],
        _Binding(types=frozenset({owner_type})),
    )
    fields, _ = _method_body_field_bindings(
        source,
        statement.body,
        method_visible,
        receiver=receiver,
        exports=exports,
        known_class_fields=known_class_fields,
    )
    return fields


def _method_body_field_bindings(  # noqa: PLR0913
    source: _ParsedSource,
    statements: list[ast.stmt],
    visible: dict[str, _Binding],
    *,
    receiver: str,
    exports: dict[str, dict[str, _Binding]],
    known_class_fields: dict[str, dict[str, _Binding]],
) -> tuple[dict[str, _Binding], dict[str, _Binding]]:
    fields: dict[str, _Binding] = {}
    scope = dict(visible)
    binding_for = partial(
        _static_expression_binding_with_exports,
        bindings=scope,
        exports=exports,
        class_fields=known_class_fields,
    )
    for statement in statements:
        _record_named_expressions(_non_control_statement_expressions(statement), scope, binding_for)
        if isinstance(statement, ast.Import | ast.ImportFrom):
            scope.update(_import_statement_exports(source, statement, exports))
            continue
        if isinstance(statement, ast.AnnAssign):
            binding = _merge_bindings(
                _annotation_binding(statement.annotation, scope, exports),
                binding_for(statement.value),
            )
            fields, scope = _record_method_assignment(
                statement.target,
                binding,
                fields=fields,
                scope=scope,
                receiver=receiver,
            )
            continue
        if isinstance(statement, ast.Assign):
            for assignment_target in statement.targets:
                for target, binding in _assignment_bindings(
                    assignment_target, statement.value, binding_for
                ):
                    fields, scope = _record_method_assignment(
                        target,
                        binding,
                        fields=fields,
                        scope=scope,
                        receiver=receiver,
                    )
            continue
        if isinstance(statement, ast.Try | ast.TryStar):
            try_fields, try_scope = _method_try_field_bindings(
                source,
                statement,
                scope,
                receiver=receiver,
                exports=exports,
                known_class_fields=known_class_fields,
            )
            fields = _merge_field_maps(fields, try_fields)
            scope.clear()
            scope.update(try_scope)
            continue
        if isinstance(statement, ast.For | ast.AsyncFor | ast.While):
            loop_fields, loop_scope = _method_loop_field_bindings(
                source,
                statement,
                scope,
                receiver=receiver,
                exports=exports,
                known_class_fields=known_class_fields,
            )
            fields = _merge_field_maps(fields, loop_fields)
            scope.clear()
            scope.update(loop_scope)
            continue
        branch_scopes: list[dict[str, _Binding]] = []
        for block, initial_scope in _control_flow_branches(
            statement, scope, exports, known_class_fields
        ):
            branch_fields, branch_scope = _method_body_field_bindings(
                source,
                block,
                initial_scope,
                receiver=receiver,
                exports=exports,
                known_class_fields=known_class_fields,
            )
            fields = _merge_field_maps(fields, branch_fields)
            branch_scopes.append(branch_scope)
        if branch_scopes:
            joined = _join_scopes(scope, *branch_scopes)
            scope.clear()
            scope.update(joined)
    return fields, scope


def _method_try_field_bindings(  # noqa: PLR0913
    source: _ParsedSource,
    statement: ast.Try | ast.TryStar,
    initial: dict[str, _Binding],
    *,
    receiver: str,
    exports: dict[str, dict[str, _Binding]],
    known_class_fields: dict[str, dict[str, _Binding]],
) -> tuple[dict[str, _Binding], dict[str, _Binding]]:
    body_fields, body_scope = _method_body_field_bindings(
        source,
        statement.body,
        initial,
        receiver=receiver,
        exports=exports,
        known_class_fields=known_class_fields,
    )
    success_fields, success_scope = _method_body_field_bindings(
        source,
        statement.orelse,
        body_scope,
        receiver=receiver,
        exports=exports,
        known_class_fields=known_class_fields,
    )
    body_prefixes = _method_possible_prefix_scopes(
        source,
        statement.body,
        initial,
        receiver=receiver,
        exports=exports,
        known_class_fields=known_class_fields,
    )
    possible_handler_input = _join_scopes(*body_prefixes)
    handler_results: list[tuple[dict[str, _Binding], dict[str, _Binding]]] = []
    for handler in statement.handlers:
        handler_input = dict(possible_handler_input)
        if handler.name is not None:
            handler_input[handler.name] = _Binding()
        handler_results.append(
            _method_body_field_bindings(
                source,
                handler.body,
                handler_input,
                receiver=receiver,
                exports=exports,
                known_class_fields=known_class_fields,
            )
        )
    joined = _join_scopes(
        initial,
        *body_prefixes,
        success_scope,
        *(handler_scope for _, handler_scope in handler_results),
    )
    final_fields, final_scope = _method_body_field_bindings(
        source,
        statement.finalbody,
        joined,
        receiver=receiver,
        exports=exports,
        known_class_fields=known_class_fields,
    )
    return (
        _merge_field_maps(
            body_fields,
            success_fields,
            *(handler_fields for handler_fields, _ in handler_results),
            final_fields,
        ),
        final_scope,
    )


def _method_possible_prefix_scopes(  # noqa: PLR0913
    source: _ParsedSource,
    statements: list[ast.stmt],
    initial: dict[str, _Binding],
    *,
    receiver: str,
    exports: dict[str, dict[str, _Binding]],
    known_class_fields: dict[str, dict[str, _Binding]],
) -> tuple[dict[str, _Binding], ...]:
    current = dict(initial)
    prefixes: list[dict[str, _Binding]] = [current]
    for statement in statements:
        branch_seed = dict(current)
        branches = _control_flow_branches(
            statement,
            branch_seed,
            exports,
            known_class_fields,
        )
        for block, branch_scope in branches:
            prefixes.extend(
                _method_possible_prefix_scopes(
                    source,
                    block,
                    branch_scope,
                    receiver=receiver,
                    exports=exports,
                    known_class_fields=known_class_fields,
                )
            )
        _, current = _method_body_field_bindings(
            source,
            [statement],
            current,
            receiver=receiver,
            exports=exports,
            known_class_fields=known_class_fields,
        )
        prefixes.append(current)
    return tuple(prefixes)


def _method_loop_field_bindings(  # noqa: PLR0913
    source: _ParsedSource,
    statement: ast.For | ast.AsyncFor | ast.While,
    initial: dict[str, _Binding],
    *,
    receiver: str,
    exports: dict[str, dict[str, _Binding]],
    known_class_fields: dict[str, dict[str, _Binding]],
) -> tuple[dict[str, _Binding], dict[str, _Binding]]:
    before_loop = dict(initial)
    binding_for = partial(
        _static_expression_binding_with_exports,
        bindings=before_loop,
        exports=exports,
        class_fields=known_class_fields,
    )
    header = statement.iter if isinstance(statement, ast.For | ast.AsyncFor) else statement.test
    _record_named_expressions((header,), before_loop, binding_for)
    fields: dict[str, _Binding] = {}
    repeated = dict(before_loop)
    break_scopes: list[dict[str, _Binding]] = []
    for _ in range(max(1, len(statement.body) + len(before_loop) + 1)):
        body_input = dict(repeated)
        if isinstance(statement, ast.For | ast.AsyncFor):
            body_binding_for = partial(
                _static_expression_binding_with_exports,
                bindings=body_input,
                exports=exports,
                class_fields=known_class_fields,
            )
            for target, binding in _iterated_target_bindings(
                statement.target, statement.iter, body_binding_for
            ):
                body_input.update(_scope_target_bindings(target, binding))
        body_fields, _ = _method_body_field_bindings(
            source,
            statement.body,
            body_input,
            receiver=receiver,
            exports=exports,
            known_class_fields=known_class_fields,
        )
        fields = _merge_field_maps(fields, body_fields)
        jumps = _collect_block_jumps(
            source,
            statement.body,
            exports,
            known_class_fields,
            body_input,
        )
        break_scopes.extend(jumps.breaks)
        back_edges = (
            *((jumps.fallthrough,) if jumps.fallthrough is not None else ()),
            *jumps.continues,
        )
        updated = _join_scopes(before_loop, *back_edges)
        if updated == repeated:
            break
        repeated = updated
    else_fields, else_scope = _method_body_field_bindings(
        source,
        statement.orelse,
        repeated,
        receiver=receiver,
        exports=exports,
        known_class_fields=known_class_fields,
    )
    return _merge_field_maps(fields, else_fields), _join_scopes(else_scope, *break_scopes)


def _changed_scope_bindings(
    before: dict[str, _Binding],
    after: dict[str, _Binding],
) -> dict[str, _Binding]:
    return {
        name: binding
        for name, binding in after.items()
        if name not in before or binding != before[name]
    }


def _record_method_assignment(
    target: ast.AST,
    binding: _Binding,
    *,
    fields: dict[str, _Binding],
    scope: dict[str, _Binding],
    receiver: str,
) -> tuple[dict[str, _Binding], dict[str, _Binding]]:
    name = _assignment_name(target)
    if name is not None:
        scope[name] = binding
    return (
        _merge_field_maps(fields, _class_field_for_binding(target, binding, receiver)),
        scope,
    )


def _assignment_bindings(
    target: ast.AST,
    value: ast.AST,
    binding_for: Callable[[ast.AST], _Binding],
) -> tuple[tuple[ast.AST, _Binding], ...]:
    if isinstance(target, ast.Tuple | ast.List) and isinstance(value, ast.Tuple | ast.List):
        starred = tuple(
            index for index, element in enumerate(target.elts) if isinstance(element, ast.Starred)
        )
        if not starred and len(target.elts) == len(value.elts):
            return tuple(
                pair
                for element, element_value in zip(target.elts, value.elts, strict=True)
                for pair in _assignment_bindings(element, element_value, binding_for)
            )
        if len(starred) == 1 and len(value.elts) >= len(target.elts) - 1:
            starred_index = starred[0]
            trailing = len(target.elts) - starred_index - 1
            pairs = tuple(
                pair
                for element, element_value in zip(
                    target.elts[:starred_index], value.elts[:starred_index], strict=True
                )
                for pair in _assignment_bindings(element, element_value, binding_for)
            )
            if trailing:
                pairs += tuple(
                    pair
                    for element, element_value in zip(
                        target.elts[-trailing:], value.elts[-trailing:], strict=True
                    )
                    for pair in _assignment_bindings(element, element_value, binding_for)
                )
            middle = value.elts[starred_index : len(value.elts) - trailing if trailing else None]
            return (
                *pairs,
                (
                    target.elts[starred_index],
                    _merge_bindings(*(binding_for(element) for element in middle)),
                ),
            )
    binding = binding_for(value)
    if isinstance(target, ast.Tuple | ast.List):
        return tuple(pair for element in target.elts for pair in _target_bindings(element, binding))
    return ((target, binding),)


def _target_bindings(
    target: ast.AST,
    binding: _Binding,
) -> tuple[tuple[ast.AST, _Binding], ...]:
    if isinstance(target, ast.Tuple | ast.List):
        return tuple(pair for element in target.elts for pair in _target_bindings(element, binding))
    return ((target, binding),)


def _assignment_name(target: ast.AST) -> str | None:
    while isinstance(target, ast.Starred):
        target = target.value
    return target.id if isinstance(target, ast.Name) else None


def _class_field_for_binding(
    target: ast.AST,
    binding: _Binding,
    receiver: str,
) -> dict[str, _Binding]:
    if isinstance(target, ast.Starred):
        return _class_field_for_binding(target.value, binding, receiver)
    if isinstance(target, ast.Attribute) and _receiver_attribute(target, receiver):
        return {target.attr: binding}
    return {}


def _narrowed_scope(
    scope: dict[str, _Binding],
    none_guard: tuple[str, bool] | None,
    awareness_guard: tuple[str, bool] | None,
    *,
    when_true: bool,
) -> dict[str, _Binding]:
    narrowed = dict(scope)
    if none_guard is not None:
        name, non_none_when_true = none_guard
        if name in narrowed and when_true is non_none_when_true:
            narrowed[name] = replace(narrowed[name], can_be_none=False)
    if awareness_guard is not None:
        name, aware_when_true = awareness_guard
        if name in narrowed and when_true is aware_when_true:
            narrowed[name] = replace(narrowed[name], can_be_naive=False)
    return narrowed


def _join_scopes(*scopes: dict[str, _Binding]) -> dict[str, _Binding]:
    names = frozenset().union(*(scope.keys() for scope in scopes))
    return {
        name: _merge_bindings(*(scope[name] for scope in scopes if name in scope)) for name in names
    }


def _block_falls_through(statements: list[ast.stmt]) -> bool:  # noqa: PLR0911
    if not statements:
        return True
    final = statements[-1]
    if isinstance(final, ast.Break | ast.Continue | ast.Raise | ast.Return):
        return False
    if isinstance(final, ast.If):
        return _block_falls_through(final.body) or _block_falls_through(final.orelse)
    if isinstance(final, ast.With | ast.AsyncWith):
        return _block_falls_through(final.body)
    if isinstance(final, ast.Try | ast.TryStar):
        if final.finalbody and not _block_falls_through(final.finalbody):
            return False
        return _block_falls_through(final.body) or any(
            _block_falls_through(handler.body) for handler in final.handlers
        )
    return True


def _forbidden_import(imported_name: str) -> _Prohibition | None:
    for module, rule_id, kind in _FORBIDDEN_IMPORTS:
        if imported_name == module or imported_name.startswith(f"{module}."):
            return (rule_id, kind, module)
    return None


def _wildcard_prohibition(module: str) -> _Prohibition | None:
    matches = tuple(
        (prefix, prohibition)
        for prefix, prohibition in _WILDCARD_PROHIBITIONS.items()
        if module == prefix or module.startswith(f"{prefix}.")
    )
    if not matches:
        return None
    return max(matches, key=lambda item: len(item[0]))[1]


def _prohibition_for_reference(  # noqa: PLR0911
    qualified_name: str,
) -> _Prohibition | None:
    if qualified_name in _AMBIENT_TIME_CALLS:
        return ("CAP001", "ambient clock reference", qualified_name)
    if qualified_name in _RANDOM_CALLS or qualified_name in _SYSTEM_RANDOM_CALLS:
        return ("CAP002", "ambient randomness reference", qualified_name)
    if qualified_name in _AMBIENT_UUID_CALLS:
        return ("CAP003", "ambient identifier reference", qualified_name)
    if qualified_name in _LOCAL_TIME_CALLS:
        return ("CAP004", "host-local time reference", qualified_name)
    if (
        qualified_name in _ENVIRONMENT_CALLS
        or qualified_name in _ENVIRONMENT_OBJECTS
        or qualified_name in _AMBIENT_ENVIRONMENT_VALUES
    ):
        return ("CAP004", "environment access", qualified_name)
    if (
        qualified_name in _NETWORK_CALLS
        or (
            qualified_name.startswith("asyncio.AbstractEventLoop.")
            and qualified_name.rsplit(".", 1)[-1] in _NETWORK_LOOP_METHODS
        )
        or _typed_effect_receiver_member(qualified_name, _NETWORK_EFFECT_RECEIVER_TYPES)
    ):
        return ("CAP005", "network call", qualified_name)
    if (
        qualified_name in _FILESYSTEM_CALLS
        or (
            qualified_name.startswith("pathlib.")
            and qualified_name.rsplit(".", 1)[-1] in _PATH_EFFECT_METHODS
        )
        or _typed_effect_receiver_member(qualified_name, _FILESYSTEM_EFFECT_RECEIVER_TYPES)
    ):
        return ("CAP009", "filesystem call", qualified_name)
    if (
        qualified_name in _PROCESS_CALLS
        or (
            qualified_name.startswith("asyncio.AbstractEventLoop.")
            and qualified_name.rsplit(".", 1)[-1] in _PROCESS_LOOP_METHODS
        )
        or _typed_effect_receiver_member(qualified_name, _PROCESS_EFFECT_RECEIVER_TYPES)
    ):
        return ("CAP010", "external-process call", qualified_name)
    return None


def _typed_effect_receiver_member(
    qualified_name: str,
    receiver_types: frozenset[str],
) -> bool:
    return any(qualified_name.startswith(f"{receiver_type}.") for receiver_type in receiver_types)


def _builtin_protocol(names: frozenset[str]) -> str | None:
    protocols = (
        ({"aiter", "builtins.aiter"}, "__aiter__"),
        ({"anext", "builtins.anext"}, "__anext__"),
        ({"iter", "builtins.iter"}, "__iter__"),
        ({"next", "builtins.next"}, "__next__"),
        ({"len", "builtins.len"}, "__len__"),
        ({"bool", "builtins.bool", "operator.truth"}, "__bool__"),
        (
            {
                "bytearray",
                "builtins.bytearray",
                "builtins.bytes",
                "builtins.memoryview",
                "bytes",
                "memoryview",
            },
            "__buffer__",
        ),
    )
    return next((protocol for builtins, protocol in protocols if names & builtins), None)


def _effect_receiver_protocol_prohibitions(
    binding: _Binding,
    protocol: str,
) -> tuple[_Prohibition, ...]:
    families = (
        (_NETWORK_EFFECT_RECEIVER_TYPES, "CAP005", "network call"),
        (_FILESYSTEM_EFFECT_RECEIVER_TYPES, "CAP009", "filesystem call"),
        (_PROCESS_EFFECT_RECEIVER_TYPES, "CAP010", "external-process call"),
    )
    return tuple(
        (rule_id, kind, f"{receiver_type}.{protocol}")
        for receiver_types, rule_id, kind in families
        for receiver_type in sorted(binding.types & receiver_types)
    )


def _prohibited_calls(
    node: ast.Call,
    callable_binding: _Binding,
    receiver: _Binding | None,
    binding_for: Callable[[ast.AST | None], _Binding],
) -> tuple[_Prohibition, ...]:
    prohibitions: set[_Prohibition] = set()
    for qualified_name in callable_binding.names:
        prohibitions.update(
            _qualified_call_prohibitions(
                node,
                qualified_name,
                binding_for=binding_for,
            )
        )
    if callable_binding.requires_seed and not _has_non_nullable_argument(
        node, 0, frozenset({"x", "seed"}), binding_for
    ):
        prohibitions.add(
            ("CAP002", "ambient randomness call", "random.Random subclass without an explicit seed")
        )
    if "random.Random.seed" in callable_binding.names:
        bound_seed = "random.Random" in callable_binding.method_receiver_types or bool(
            receiver is not None and "random.Random" in receiver.types
        )
        seed_position = 0 if bound_seed else 1
        if not _has_non_nullable_argument(
            node, seed_position, frozenset({"a", "x", "seed"}), binding_for
        ):
            prohibitions.add(
                ("CAP002", "ambient randomness call", "random.Random.seed without an explicit seed")
            )
    if receiver is not None and isinstance(node.func, ast.Attribute):
        if receiver.types & _PATH_TYPES and node.func.attr in _PATH_EFFECT_METHODS:
            prohibitions.add(("CAP009", "filesystem call", f"pathlib.Path.{node.func.attr}"))
        if receiver.types & _EVENT_LOOP_TYPES:
            if node.func.attr in _NETWORK_LOOP_METHODS:
                prohibitions.add(
                    ("CAP005", "network call", f"asyncio.AbstractEventLoop.{node.func.attr}")
                )
            if node.func.attr in _PROCESS_LOOP_METHODS:
                prohibitions.add(
                    (
                        "CAP010",
                        "external-process call",
                        f"asyncio.AbstractEventLoop.{node.func.attr}",
                    )
                )
    prohibitions.update(
        _datetime_receiver_prohibitions(
            node,
            callable_binding,
            receiver,
            binding_for=binding_for,
        )
    )
    return tuple(sorted(prohibitions))


def _qualified_call_prohibitions(
    node: ast.Call,
    qualified_name: str,
    *,
    binding_for: Callable[[ast.AST | None], _Binding],
) -> tuple[_Prohibition, ...]:
    prohibitions: set[_Prohibition] = set()
    dependency = _forbidden_import(qualified_name)
    if dependency is not None:
        prohibitions.add(dependency)
    direct = _prohibition_for_reference(qualified_name)
    if direct is not None:
        prohibitions.add(direct)
    optional_time = _OPTIONAL_AMBIENT_TIME_CALLS.get(qualified_name)
    if optional_time is not None and not _has_non_nullable_argument(
        node, *optional_time, binding_for
    ):
        prohibitions.add(("CAP001", "ambient clock call", qualified_name))
    optional_local = _OPTIONAL_LOCAL_TIME_CALLS.get(qualified_name)
    if optional_local is not None and not _has_non_nullable_argument(
        node, *optional_local, binding_for
    ):
        prohibitions.add(("CAP004", "host-local time call", qualified_name))
    filesystem_keywords = _OPTIONAL_FILESYSTEM_CALLS.get(qualified_name)
    if filesystem_keywords is not None and _has_potentially_truthy_keyword_argument(
        node, filesystem_keywords, binding_for
    ):
        prohibitions.add(("CAP009", "filesystem call", qualified_name))
    environment_object = _environment_object_for_call(qualified_name)
    if environment_object is not None:
        prohibitions.add(("CAP004", "environment access", environment_object))
    if qualified_name == "random.Random" and not _has_non_nullable_argument(
        node, 0, frozenset({"x", "seed"}), binding_for
    ):
        prohibitions.add(
            ("CAP002", "ambient randomness call", "random.Random without an explicit seed")
        )
    return tuple(prohibitions)


def _datetime_receiver_prohibitions(
    node: ast.Call,
    callable_binding: _Binding,
    receiver: _Binding | None,
    *,
    binding_for: Callable[[ast.AST | None], _Binding],
) -> tuple[_Prohibition, ...]:
    methods = callable_binding.names & {
        "datetime.datetime.astimezone",
        "datetime.datetime.timestamp",
    }
    if not methods:
        return ()
    if "datetime.datetime" in callable_binding.method_receiver_types:
        effective_receiver = _Binding(
            types=frozenset({"datetime.datetime"}),
            can_be_naive=callable_binding.method_receiver_can_be_naive,
        )
        argument_offset = 0
    elif receiver is not None and "datetime.datetime" in receiver.types:
        effective_receiver = receiver
        argument_offset = 0
    else:
        receiver_argument = _call_argument(node, 0, frozenset())
        if receiver_argument is None:
            return ()
        effective_receiver = binding_for(receiver_argument)
        argument_offset = 1
    if "datetime.datetime" not in effective_receiver.types:
        return ()
    if "datetime.datetime.astimezone" in methods and (
        effective_receiver.can_be_naive
        or not _has_non_nullable_argument(node, argument_offset, frozenset({"tz"}), binding_for)
    ):
        return (("CAP004", "host-local time call", "datetime.datetime.astimezone"),)
    if "datetime.datetime.timestamp" in methods and effective_receiver.can_be_naive:
        return (("CAP004", "host-local time call", "datetime.datetime.timestamp"),)
    return ()


def _environment_object_for_call(qualified_name: str) -> str | None:
    for environment_object in _ENVIRONMENT_OBJECTS:
        if qualified_name.startswith(f"{environment_object}."):
            return environment_object
    return None


def _has_non_nullable_argument(
    node: ast.Call,
    position: int,
    keyword_names: frozenset[str],
    binding_for: Callable[[ast.AST | None], _Binding],
) -> bool:
    argument = _call_argument(node, position, keyword_names)
    if argument is None:
        return False
    return not binding_for(argument).can_be_none and not _expression_can_be_none(argument)


def _has_potentially_truthy_keyword_argument(
    node: ast.Call,
    keyword_names: frozenset[str],
    binding_for: Callable[[ast.AST | None], _Binding],
) -> bool:
    argument, has_unknown_expansion = _keyword_argument(node, keyword_names)
    if argument is None:
        return has_unknown_expansion
    return not _expression_is_definitely_falsy(argument, binding_for)


def _call_argument(
    node: ast.Call,
    position: int,
    keyword_names: frozenset[str],
) -> ast.expr | None:
    positional: list[ast.expr] = []
    positional_are_known = True
    for argument in node.args:
        if not isinstance(argument, ast.Starred):
            positional.append(argument)
            continue
        if isinstance(argument.value, ast.Tuple | ast.List):
            positional.extend(argument.value.elts)
            continue
        positional_are_known = False
    if positional_are_known and len(positional) > position:
        return positional[position]
    keyword_argument, _ = _keyword_argument(node, keyword_names)
    return keyword_argument


def _keyword_argument(
    node: ast.Call,
    keyword_names: frozenset[str],
) -> tuple[ast.expr | None, bool]:
    unknown_expansion = False
    for keyword in node.keywords:
        if keyword.arg in keyword_names:
            return keyword.value, unknown_expansion
        if keyword.arg is not None:
            continue
        argument, mapping_is_unknown = _mapping_keyword_argument(
            keyword.value,
            keyword_names,
        )
        if argument is not None:
            return argument, unknown_expansion or mapping_is_unknown
        unknown_expansion = unknown_expansion or mapping_is_unknown
    return None, unknown_expansion


def _mapping_keyword_argument(
    expression: ast.expr,
    keyword_names: frozenset[str],
) -> tuple[ast.expr | None, bool]:
    if not isinstance(expression, ast.Dict):
        return None, True
    found: ast.expr | None = None
    unknown_expansion = False
    for key, value in zip(expression.keys, expression.values, strict=True):
        if key is None:
            nested, nested_is_unknown = _mapping_keyword_argument(value, keyword_names)
            if nested is not None:
                found = nested
            unknown_expansion = unknown_expansion or nested_is_unknown
            continue
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            unknown_expansion = True
            continue
        if key.value in keyword_names:
            found = value
    return found, unknown_expansion


def _expression_is_definitely_falsy(
    expression: ast.AST,
    binding_for: Callable[[ast.AST | None], _Binding],
) -> bool:
    if isinstance(expression, ast.Constant):
        return not bool(expression.value)
    if isinstance(expression, ast.Tuple | ast.List | ast.Set | ast.Dict):
        return not expression.elts if not isinstance(expression, ast.Dict) else not expression.keys
    if isinstance(expression, ast.IfExp):
        return _expression_is_definitely_falsy(
            expression.body, binding_for
        ) and _expression_is_definitely_falsy(expression.orelse, binding_for)
    string_values = binding_for(expression).string_values
    return bool(string_values) and all(not value for value in string_values)


def _expression_can_be_none(expression: ast.AST) -> bool:
    if isinstance(expression, ast.Constant):
        return expression.value is None
    if isinstance(expression, ast.IfExp):
        guarded = _none_guard(expression.test)
        if guarded is not None:
            name, non_none_when_true = guarded
            if (
                non_none_when_true
                and isinstance(expression.body, ast.Name)
                and expression.body.id == name
            ):
                return _expression_can_be_none(expression.orelse)
            if (
                not non_none_when_true
                and isinstance(expression.orelse, ast.Name)
                and expression.orelse.id == name
            ):
                return _expression_can_be_none(expression.body)
        return _expression_can_be_none(expression.body) or _expression_can_be_none(
            expression.orelse
        )
    if isinstance(expression, ast.BoolOp) and isinstance(expression.op, ast.Or):
        return all(_expression_can_be_none(value) for value in expression.values)
    return False


def _none_guard(expression: ast.AST) -> tuple[str, bool] | None:
    if not isinstance(expression, ast.Compare) or len(expression.ops) != 1:
        return None
    if len(expression.comparators) != 1:
        return None
    left = expression.left
    right = expression.comparators[0]
    if isinstance(left, ast.Name) and isinstance(right, ast.Constant) and right.value is None:
        if isinstance(expression.ops[0], ast.IsNot):
            return (left.id, True)
        if isinstance(expression.ops[0], ast.Is):
            return (left.id, False)
    return None


def _datetime_awareness_guard(expression: ast.AST) -> tuple[str, bool] | None:
    if not isinstance(expression, ast.Compare) or len(expression.ops) != 1:
        return None
    if len(expression.comparators) != 1:
        return None
    left = expression.left
    right = expression.comparators[0]
    if not (
        isinstance(left, ast.Call)
        and isinstance(left.func, ast.Attribute)
        and left.func.attr == "utcoffset"
        and isinstance(left.func.value, ast.Name)
        and not left.args
        and not left.keywords
        and isinstance(right, ast.Constant)
        and right.value is None
    ):
        return None
    if isinstance(expression.ops[0], ast.IsNot):
        return (left.func.value.id, True)
    if isinstance(expression.ops[0], ast.Is):
        return (left.func.value.id, False)
    return None


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reject ambient effects in capability modules.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    package_root = arguments.root / "src" / PACKAGE_NAME
    if not package_root.is_dir():
        print("capability package is unavailable", file=sys.stderr)  # noqa: T201
        return 2
    violations = inspect_capability_dependencies(package_root)
    if violations:
        for violation in violations:
            print(violation.format(), file=sys.stderr)  # noqa: T201
        return 1
    print(  # noqa: T201
        f"validated {len(protected_source_files(package_root))} protected production files"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

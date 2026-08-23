from __future__ import annotations

from pathlib import Path

import pytest
from scripts.check_capability_dependencies import (
    GOVERNING_RULE,
    inspect_capability_dependencies,
    main,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPOSITORY_ROOT / "src" / "agentic_investment_os"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "capability_dependencies"
UNAVAILABLE_EXIT_CODE = 2
type ExpectedDiagnostic = tuple[int, int, str, str]

EXPECTED_DIAGNOSTICS: dict[str, tuple[ExpectedDiagnostic, ...]] = {
    "ambient_datetime.py.txt": (
        (5, 15, "CAP004", "datetime.datetime.astimezone"),
        (6, 13, "CAP004", "datetime.datetime.timestamp"),
        (7, 23, "CAP004", "datetime.datetime.astimezone"),
        (8, 21, "CAP004", "datetime.datetime.timestamp"),
        (9, 22, "CAP004", "datetime.datetime.timestamp"),
        (10, 24, "CAP004", "datetime.datetime.timestamp"),
        (11, 21, "CAP004", "datetime.datetime.timestamp"),
        (12, 24, "CAP004", "datetime.datetime.timestamp"),
    ),
    "assignment_expressions.py.txt": (
        (6, 1, "CAP002", "random.Random without an explicit seed"),
        (7, 1, "CAP002", "random.Random without an explicit seed"),
        (8, 1, "CAP009", "pathlib.Path.read_text"),
        (9, 1, "CAP004", "datetime.datetime.timestamp"),
    ),
    "ambient_event_loop_time.py.txt": ((5, 12, "CAP001", "asyncio.AbstractEventLoop.time"),),
    "broker_client.py.txt": (
        (1, 1, "CAP007", "alpaca"),
        (1, 35, "CAP007", "alpaca"),
    ),
    "clock_submodule_wildcard.py.txt": (
        (1, 28, "CAP001", "asyncio.events"),
        (2, 33, "CAP001", "asyncio.unix_events"),
        (3, 36, "CAP001", "asyncio.windows_events"),
    ),
    "control_target_bindings.py.txt": (
        (7, 5, "CAP009", "pathlib.Path.read_text"),
        (10, 5, "CAP002", "random.Random without an explicit seed"),
        (13, 5, "CAP001", "asyncio.AbstractEventLoop.time"),
        (16, 5, "CAP009", "pathlib.Path.read_text"),
        (17, 5, "CAP002", "random.Random without an explicit seed"),
        (21, 5, "CAP002", "random.Random without an explicit seed"),
    ),
    "class_field_factories.py.txt": (
        (11, 16, "CAP009", "pathlib.Path.read_text"),
        (21, 16, "CAP001", "asyncio.AbstractEventLoop.time"),
        (31, 9, "CAP002", "random.Random.seed without an explicit seed"),
        (41, 16, "CAP004", "datetime.datetime.timestamp"),
    ),
    "class_pattern_protocols.py.txt": (
        (14, 20, "CAP009", "gzip.GzipFile.__iter__"),
        (17, 26, "CAP009", "shelve.Shelf.__getitem__"),
        (28, 29, "CAP009", "shelve.Shelf.__getitem__"),
        (41, 30, "CAP009", "gzip.GzipFile.__iter__"),
    ),
    "comprehension_targets.py.txt": (
        (4, 15, "CAP009", "pathlib.Path.read_text"),
        (5, 15, "CAP002", "random.Random without an explicit seed"),
        (6, 13, "CAP009", "pathlib.Path.read_text"),
        (7, 24, "CAP009", "pathlib.Path.read_text"),
        (8, 19, "CAP009", "pathlib.Path.read_text"),
        (12, 1, "CAP002", "random.Random without an explicit seed"),
        (15, 1, "CAP002", "random.Random without an explicit seed"),
        (18, 1, "CAP002", "random.Random without an explicit seed"),
        (21, 1, "CAP002", "random.Random without an explicit seed"),
        (24, 1, "CAP002", "random.Random without an explicit seed"),
    ),
    "effect_aliases.py.txt": (
        (14, 1, "CAP002", "random.Random without an explicit seed"),
        (15, 1, "CAP009", "pathlib.Path.read_text"),
        (16, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (17, 1, "CAP005", "asyncio.AbstractEventLoop.create_connection"),
        (18, 1, "CAP010", "asyncio.AbstractEventLoop.subprocess_exec"),
        (19, 1, "CAP001", "datetime.datetime.now"),
    ),
    "effect_subclasses.py.txt": (
        (23, 1, "CAP001", "datetime.datetime.now"),
        (24, 1, "CAP004", "datetime.datetime.timestamp"),
        (25, 1, "CAP002", "random.Random subclass without an explicit seed"),
        (26, 1, "CAP009", "pathlib.Path.write_text"),
        (27, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (28, 1, "CAP005", "asyncio.AbstractEventLoop.create_connection"),
        (29, 1, "CAP010", "asyncio.AbstractEventLoop.subprocess_exec"),
        (36, 1, "CAP001", "datetime.date.today"),
        (37, 1, "CAP004", "datetime.date.fromtimestamp"),
    ),
    "effect_receiver_protocols.py.txt": (
        (8, 9, "CAP009", "shelve.Shelf.__getitem__"),
        (9, 1, "CAP009", "shelve.Shelf.__setitem__"),
        (11, 8, "CAP009", "mmap.mmap.__getitem__"),
        (13, 13, "CAP009", "fileinput.FileInput.__iter__"),
        (16, 1, "CAP009", "gzip.GzipFile.__next__"),
        (17, 1, "CAP009", "gzip.GzipFile.__iter__"),
        (18, 27, "CAP009", "gzip.GzipFile.__iter__"),
        (20, 6, "CAP009", "zipfile.ZipFile.__enter__"),
        (23, 15, "CAP009", "gzip.GzipFile.__iter__"),
        (24, 12, "CAP009", "gzip.GzipFile.__iter__"),
        (31, 7, "CAP009", "gzip.GzipFile.__iter__"),
        (35, 16, "CAP009", "gzip.GzipFile.__iter__"),
        (38, 16, "CAP009", "gzip.GzipFile.__iter__"),
        (39, 8, "CAP009", "shelve.Shelf.__iter__"),
        (40, 25, "CAP009", "shelve.Shelf.__contains__"),
        (41, 14, "CAP009", "shelve.Shelf.__iter__"),
        (42, 8, "CAP009", "shelve.Shelf.__iter__"),
        (43, 8, "CAP009", "mmap.mmap.__buffer__"),
        (49, 5, "CAP005", "asyncio.StreamReader.__aiter__"),
        (50, 11, "CAP005", "asyncio.StreamReader.__anext__"),
        (53, 22, "CAP009", "gzip.GzipFile.__iter__"),
        (55, 40, "CAP009", "gzip.GzipFile.__iter__"),
        (57, 10, "CAP009", "shelve.Shelf.__getitem__"),
        (60, 41, "CAP009", "gzip.GzipFile.__iter__"),
        (61, 47, "CAP009", "gzip.GzipFile.__iter__"),
        (64, 64, "CAP009", "gzip.GzipFile.__iter__"),
        (67, 11, "CAP009", "shelve.Shelf.__getitem__"),
        (70, 11, "CAP009", "gzip.GzipFile.__iter__"),
        (73, 20, "CAP009", "shelve.Shelf.__getitem__"),
        (76, 19, "CAP009", "gzip.GzipFile.__iter__"),
        (79, 5, "CAP009", "gzip.GzipFile.__iter__"),
        (80, 5, "CAP009", "fileinput.FileInput.__iter__"),
        (82, 30, "CAP009", "gzip.GzipFile.__iter__"),
        (84, 14, "CAP009", "shelve.Shelf.__getitem__"),
        (87, 14, "CAP009", "shelve.Shelf.__getitem__"),
        (89, 4, "CAP009", "shelve.Shelf.__bool__"),
        (91, 7, "CAP009", "shelve.Shelf.__bool__"),
        (93, 8, "CAP009", "shelve.Shelf.__bool__"),
        (94, 23, "CAP009", "shelve.Shelf.__bool__"),
        (95, 12, "CAP009", "shelve.Shelf.__bool__"),
        (96, 15, "CAP009", "shelve.Shelf.__bool__"),
        (97, 38, "CAP009", "shelve.Shelf.__bool__"),
        (99, 15, "CAP009", "shelve.Shelf.__bool__"),
        (104, 6, "CAP009", "shelve.Shelf.__bool__"),
        (105, 6, "CAP009", "shelve.Shelf.__bool__"),
        (106, 20, "CAP009", "shelve.Shelf.__bool__"),
        (107, 1, "CAP009", "shelve.Shelf.__bool__"),
        (109, 1, "CAP009", "shelve.Shelf.__bool__"),
        (112, 7, "CAP009", "shelve.Shelf.__bool__"),
        (119, 15, "CAP009", "shelve.Shelf.__bool__"),
        (126, 8, "CAP009", "shelve.Shelf.__bool__"),
    ),
    "environment.py.txt": (
        (3, 9, "CAP004", "os.getenv"),
        (4, 1, "CAP004", "os.putenv"),
        (5, 1, "CAP004", "os.unsetenv"),
        (6, 1, "CAP004", "os.environ"),
        (7, 5, "CAP004", "os.environ"),
        (8, 13, "CAP004", "os.getenv"),
        (10, 21, "CAP004", "os.getenv"),
        (11, 9, "CAP004", "os.getenv"),
        (12, 5, "CAP004", "os.environ"),
        (16, 30, "CAP004", "os.environ"),
    ),
    "environment_local_timezone.py.txt": (
        (5, 15, "CAP004", "time.localtime"),
        (6, 8, "CAP004", "time.ctime"),
        (7, 13, "CAP004", "time.mktime"),
        (8, 14, "CAP004", "datetime.date.fromtimestamp"),
        (9, 18, "CAP004", "datetime.datetime.fromtimestamp"),
        (10, 13, "CAP004", "datetime.datetime.astimezone"),
        (12, 12, "CAP004", "datetime.datetime.fromtimestamp"),
        (13, 16, "CAP004", "datetime.datetime.fromtimestamp"),
        (15, 20, "CAP004", "datetime.datetime.fromtimestamp"),
    ),
    "event_loop_summary.py.txt": ((6, 16, "CAP001", "asyncio.AbstractEventLoop.time"),),
    "filesystem.py.txt": ((3, 11, "CAP009", "pathlib.Path.read_text"),),
    "filesystem_dataclass.py.txt": (
        (10, 16, "CAP009", "pathlib.Path.read_text"),
        (14, 12, "CAP009", "pathlib.Path.read_text"),
        (19, 16, "CAP009", "pathlib.Path.read_text"),
        (34, 12, "CAP009", "pathlib.Path.read_text"),
        (39, 16, "CAP009", "pathlib.Path.read_text"),
        (49, 16, "CAP009", "pathlib.Path.read_text"),
        (57, 16, "CAP009", "pathlib.Path.read_text"),
        (65, 16, "CAP009", "pathlib.Path.read_text"),
        (70, 16, "CAP009", "pathlib.Path.read_text"),
        (83, 16, "CAP009", "pathlib.Path.read_text"),
        (101, 12, "CAP009", "pathlib.Path.read_text"),
        (106, 16, "CAP009", "pathlib.Path.read_text"),
        (119, 16, "CAP009", "pathlib.Path.read_text"),
        (124, 16, "CAP009", "pathlib.Path.read_text"),
        (134, 16, "CAP009", "pathlib.Path.read_text"),
        (145, 16, "CAP009", "pathlib.Path.read_text"),
    ),
    "filesystem_destructure.py.txt": (
        (5, 1, "CAP009", "pathlib.Path.read_text"),
        (7, 1, "CAP009", "pathlib.Path.read_text"),
    ),
    "filesystem_guard.py.txt": ((9, 5, "CAP009", "pathlib.Path.write_text"),),
    "filesystem_openers.py.txt": (
        *(
            (line, 1, "CAP009", subject)
            for line, subject in (
                *enumerate(
                    (
                        "aifc.open",
                        "bz2.open",
                        "codecs.open",
                        "dbm.open",
                        "gzip.open",
                        "linecache.getline",
                        "logging.FileHandler",
                        "logging.handlers.WatchedFileHandler",
                        "logging.handlers.RotatingFileHandler",
                        "logging.handlers.TimedRotatingFileHandler",
                        "lzma.open",
                        "shelve.open",
                        "sndhdr.what",
                        "sunau.open",
                        "tarfile.TarFile",
                        "tarfile.open",
                        "tokenize.open",
                        "wave.open",
                        "xml.dom.minidom.parse",
                        "xml.etree.ElementTree.parse",
                        "xml.sax.parse",
                        "zipfile.PyZipFile",
                        "zipfile.ZipFile",
                    ),
                    start=21,
                ),
                (53, "bz2.BZ2File"),
                (54, "dbm.dumb.open"),
                (55, "fileinput.FileInput"),
                (56, "gzip.GzipFile"),
                (57, "logging.basicConfig"),
                (58, "logging.config.fileConfig"),
                (59, "lzma.LZMAFile"),
                (60, "mailbox.Babyl"),
                (61, "mailbox.Maildir"),
                (62, "mailbox.MH"),
                (63, "mailbox.MMDF"),
                (64, "mailbox.mbox"),
                (65, "xml.etree.ElementTree.iterparse"),
                (66, "shelve.DbfilenameShelf"),
                (67, "dbm.gnu.open"),
                (68, "dbm.ndbm.open"),
                (69, "filecmp.cmp"),
                (70, "logging.basicConfig"),
                (73, "logging.basicConfig"),
                (74, "logging.basicConfig"),
                (76, "logging.basicConfig"),
                (83, "dbm.whichdb"),
                (84, "filecmp.cmpfiles"),
                (85, "filecmp.dircmp"),
                (86, "configparser.ConfigParser.read"),
                (87, "configparser.RawConfigParser.read"),
                (88, "runpy.run_path"),
                (89, "zipimport.zipimporter"),
                (90, "pkgutil.iter_modules"),
                (91, "pkgutil.walk_packages"),
                (92, "pkgutil.get_data"),
                (93, "pkgutil.extend_path"),
                (94, "pkgutil.get_importer"),
                (95, "pkgutil.iter_importers"),
                (96, "pkgutil.get_loader"),
                (97, "pkgutil.find_loader"),
                (101, "io.FileIO"),
                (102, "io.open_code"),
                (103, "aifc.Aifc_read"),
                (104, "aifc.Aifc_write"),
                (105, "sunau.Au_read"),
                (106, "sunau.Au_write"),
                (107, "wave.Wave_read"),
                (108, "wave.Wave_write"),
                (109, "zipfile.Path"),
                (113, "importlib.resources.as_file"),
                (114, "importlib.resources.contents"),
                (115, "importlib.resources.files"),
                (116, "importlib.resources.is_resource"),
                (117, "importlib.resources.open_binary"),
                (118, "importlib.resources.open_text"),
                (119, "importlib.resources.path"),
                (120, "importlib.resources.read_binary"),
                (121, "importlib.resources.read_text"),
                (123, "linecache.getlines"),
                (124, "linecache.checkcache"),
                (125, "tarfile.is_tarfile"),
                (126, "zipfile.is_zipfile"),
            )
        ),
        (128, 23, "CAP009", "linecache.checkcache"),
        (129, 23, "CAP009", "linecache.getlines"),
        (130, 21, "CAP009", "tarfile.is_tarfile"),
        (131, 21, "CAP009", "zipfile.is_zipfile"),
        (133, 1, "CAP009", "linecache.getlines"),
        (134, 1, "CAP009", "linecache.checkcache"),
        (135, 1, "CAP009", "tarfile.is_tarfile"),
        (136, 1, "CAP009", "zipfile.is_zipfile"),
    ),
    "filesystem_opener_wildcards.py.txt": tuple(
        (line, column, "CAP009", subject)
        for line, column, subject in (
            (1, 18, "aifc"),
            (2, 17, "bz2"),
            (3, 20, "codecs"),
            (4, 17, "dbm"),
            (5, 18, "gzip"),
            (6, 23, "linecache"),
            (7, 30, "logging"),
            (8, 18, "lzma"),
            (9, 20, "shelve"),
            (10, 20, "sndhdr"),
            (11, 19, "sunau"),
            (12, 21, "tarfile"),
            (13, 22, "tokenize"),
            (14, 18, "wave"),
            (15, 29, "xml"),
            (16, 21, "zipfile"),
            (17, 21, "mailbox"),
            (18, 26, "configparser"),
            (19, 21, "filecmp"),
            (20, 19, "runpy"),
            (21, 23, "zipimport"),
            (22, 21, "pkgutil"),
            (23, 33, "importlib.machinery"),
            (24, 16, "io"),
            (25, 33, "importlib.resources"),
        )
    ),
    "filesystem_python312.py.txt": (
        (6, 5, "CAP009", "os.fchdir"),
        (7, 5, "CAP009", "os.lchmod"),
        (8, 5, "CAP009", "os.umask"),
        (10, 9, "CAP009", "os.listdrives"),
        (11, 9, "CAP009", "os.listvolumes"),
        (12, 9, "CAP009", "os.listmounts"),
        (13, 9, "CAP009", "os.path.isdevdrive"),
        (16, 9, "CAP009", "pathlib.Path.is_junction"),
        (17, 9, "CAP009", "os.path.isjunction"),
        (18, 9, "CAP009", "pathlib.Path.read_text"),
    ),
    "filesystem_type_checking.py.txt": ((10, 12, "CAP009", "pathlib.Path.read_text"),),
    "filesystem_submodule_wildcard.py.txt": ((1, 21, "CAP009", "os.path"),),
    "filesystem_wildcard.py.txt": ((1, 22, "CAP009", "tempfile"),),
    "event_loop_factories.py.txt": (
        (4, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (5, 1, "CAP005", "asyncio.AbstractEventLoop.create_connection"),
        (6, 1, "CAP010", "asyncio.AbstractEventLoop.subprocess_exec"),
        (7, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (10, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (13, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (16, 5, "CAP001", "asyncio.AbstractEventLoop.time"),
        (18, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (19, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (20, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (21, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (22, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (23, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (24, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (25, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (26, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (27, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (28, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (29, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (30, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (31, 1, "CAP005", "asyncio.AbstractEventLoop.create_connection"),
        (32, 1, "CAP010", "asyncio.AbstractEventLoop.subprocess_exec"),
    ),
    "model_client.py.txt": (
        (1, 1, "CAP006", "openai"),
        (1, 20, "CAP006", "openai"),
    ),
    "model_parent.py.txt": (
        (2, 20, "CAP006", "google.genai"),
        (5, 10, "CAP006", "google.genai"),
        (6, 16, "CAP006", "google.genai"),
    ),
    "match_captures.py.txt": (
        (8, 9, "CAP009", "pathlib.Path.read_text"),
        (12, 9, "CAP002", "random.Random without an explicit seed"),
        (16, 9, "CAP001", "asyncio.AbstractEventLoop.time"),
        (20, 9, "CAP009", "pathlib.Path.read_text"),
        (29, 9, "CAP009", "pathlib.Path.read_text"),
        (39, 9, "CAP009", "pathlib.Path.read_text"),
        (52, 9, "CAP009", "pathlib.Path.read_text"),
    ),
    "mapping_captures.py.txt": (
        (6, 9, "CAP009", "gzip.GzipFile.read"),
        (9, 9, "CAP009", "gzip.GzipFile.read"),
        (12, 9, "CAP009", "gzip.GzipFile.read"),
        (17, 9, "CAP009", "gzip.GzipFile.read"),
        (21, 9, "CAP009", "gzip.GzipFile.read"),
        (30, 9, "CAP009", "gzip.GzipFile.read"),
    ),
    "network.py.txt": (
        (1, 1, "CAP005", "urllib.request"),
        (1, 28, "CAP005", "urllib.request"),
    ),
    "network_logging_handlers.py.txt": (
        (5, 1, "CAP005", "logging.handlers.SocketHandler"),
        (6, 1, "CAP005", "logging.handlers.HTTPHandler"),
        (7, 1, "CAP005", "logging.handlers.DatagramHandler"),
        (8, 1, "CAP005", "logging.handlers.SysLogHandler"),
        (9, 1, "CAP005", "logging.handlers.SMTPHandler"),
        (10, 1, "CAP005", "logging.config.listen"),
    ),
    "network_asyncio_submodule.py.txt": ((5, 11, "CAP005", "asyncio.streams.open_connection"),),
    "network_event_loop.py.txt": (
        (8, 11, "CAP005", "asyncio.AbstractEventLoop.create_connection"),
        (12, 11, "CAP005", "asyncio.AbstractEventLoop.create_connection"),
        (16, 11, "CAP005", "asyncio.AbstractEventLoop.create_connection"),
    ),
    "network_parent.py.txt": (
        (2, 20, "CAP005", "urllib.request"),
        (5, 1, "CAP005", "urllib.request"),
        (6, 1, "CAP005", "urllib.request"),
    ),
    "network_submodule_wildcard.py.txt": ((1, 29, "CAP005", "asyncio.streams"),),
    "network_wildcard.py.txt": ((1, 21, "CAP005", "asyncio"),),
    "nested_classes.py.txt": (
        (13, 1, "CAP002", "random.Random subclass without an explicit seed"),
        (14, 1, "CAP009", "pathlib.Path.read_text"),
    ),
    "process.py.txt": (
        (2, 8, "CAP010", "subprocess"),
        (5, 1, "CAP010", "subprocess"),
        (6, 1, "CAP010", "os.kill"),
        (7, 1, "CAP010", "os.waitpid"),
        (8, 1, "CAP010", "os.startfile"),
    ),
    "process_asyncio_submodule.py.txt": (
        (5, 11, "CAP010", "asyncio.subprocess.create_subprocess_exec"),
    ),
    "process_defining_module.py.txt": (
        (4, 1, "CAP010", "concurrent.futures.process.ProcessPoolExecutor"),
    ),
    "randomness.py.txt": (
        (3, 9, "CAP002", "random.random"),
        (4, 10, "CAP002", "random.binomialvariate"),
        (5, 1, "CAP002", "random.seed"),
        (6, 9, "CAP002", "random.getstate"),
        (7, 1, "CAP002", "random.setstate"),
        (8, 13, "CAP002", "random.Random without an explicit seed"),
    ),
    "randomness_branch.py.txt": ((9, 12, "CAP002", "random.Random without an explicit seed"),),
    "randomness_control_flow.py.txt": (
        (11, 12, "CAP002", "random.Random without an explicit seed"),
        (20, 12, "CAP002", "random.Random without an explicit seed"),
        (30, 12, "CAP002", "random.Random without an explicit seed"),
    ),
    "randomness_nullable.py.txt": (
        (5, 13, "CAP002", "random.Random without an explicit seed"),
        (9, 12, "CAP002", "random.Random without an explicit seed"),
        (12, 16, "CAP002", "random.Random without an explicit seed"),
        (14, 16, "CAP002", "random.Random without an explicit seed"),
    ),
    "randomness_reseed.py.txt": ((5, 1, "CAP002", "random.Random.seed without an explicit seed"),),
    "randomness_subclass.py.txt": (
        (8, 1, "CAP002", "random.Random subclass without an explicit seed"),
        (15, 12, "CAP002", "random.Random subclass without an explicit seed"),
    ),
    "randomness_type_alias.py.txt": ((10, 12, "CAP002", "random.Random without an explicit seed"),),
    "sqlite.py.txt": ((1, 8, "CAP008", "sqlite3"),),
    "starred_arguments.py.txt": (
        (5, 1, "CAP002", "random.Random without an explicit seed"),
        (7, 1, "CAP002", "random.Random.seed without an explicit seed"),
        (8, 1, "CAP004", "datetime.datetime.fromtimestamp"),
        (8, 1, "CAP004", "datetime.datetime.timestamp"),
        (9, 1, "CAP004", "datetime.datetime.astimezone"),
    ),
    "loop_exits.py.txt": (
        (10, 1, "CAP002", "random.Random without an explicit seed"),
        (17, 1, "CAP009", "pathlib.Path.read_text"),
        (25, 1, "CAP002", "random.Random without an explicit seed"),
        (36, 1, "CAP002", "random.Random without an explicit seed"),
        (45, 1, "CAP002", "random.Random without an explicit seed"),
        (53, 1, "CAP002", "random.Random without an explicit seed"),
        (63, 12, "CAP002", "random.Random without an explicit seed"),
    ),
    "typed_effect_receivers.py.txt": (
        (8, 5, "CAP005", "logging.handlers.SMTPHandler.emit"),
        (15, 9, "CAP005", "logging.handlers.DatagramHandler.emit"),
        (19, 12, "CAP009", "zipimport.zipimporter.get_data"),
        (23, 12, "CAP009", "filecmp.dircmp.left_list"),
        (27, 12, "CAP009", "importlib.machinery.SourceFileLoader.get_data"),
        (30, 1, "CAP009", "importlib.machinery.SourceFileLoader.get_data"),
    ),
    "typed_effect_receiver_members.py.txt": (
        (13, 1, "CAP005", "logging.handlers.DatagramHandler.handle"),
        (15, 1, "CAP005", "logging.handlers.HTTPHandler.handle"),
        (17, 1, "CAP005", "logging.handlers.SMTPHandler.handle"),
        (19, 1, "CAP005", "logging.handlers.SocketHandler.handle"),
        (21, 1, "CAP005", "logging.handlers.SysLogHandler.handle"),
        (22, 1, "CAP005", "logging.handlers.SocketHandler.close"),
        (23, 1, "CAP005", "logging.handlers.HTTPHandler.getConnection"),
        (26, 1, "CAP009", "logging.FileHandler.emit"),
        (28, 1, "CAP009", "zipfile.ZipFile.read"),
        (30, 1, "CAP009", "tarfile.TarFile.extractfile"),
        (32, 1, "CAP009", "gzip.GzipFile.read"),
        (34, 1, "CAP009", "bz2.BZ2File.read"),
        (36, 1, "CAP009", "lzma.LZMAFile.read"),
        (38, 1, "CAP009", "zipimport.zipimporter.invalidate_caches"),
        (40, 1, "CAP009", "importlib.machinery.ExtensionFileLoader.create_module"),
        (42, 1, "CAP009", "filecmp.dircmp.phase0"),
        (44, 1, "CAP009", "importlib.machinery.FileFinder.find_spec"),
        (45, 1, "CAP009", "importlib.machinery.PathFinder.find_spec"),
        (58, 1, "CAP009", "io.FileIO.read"),
        (60, 1, "CAP009", "zipfile.Path.read_bytes"),
        (62, 1, "CAP009", "zipfile.ZipExtFile.read"),
        (64, 1, "CAP009", "tarfile.ExFileObject.read"),
        (66, 1, "CAP009", "aifc.Aifc_read.readframes"),
        (68, 1, "CAP009", "aifc.Aifc_write.writeframes"),
        (70, 1, "CAP009", "sunau.Au_read.readframes"),
        (72, 1, "CAP009", "sunau.Au_write.writeframes"),
        (74, 1, "CAP009", "wave.Wave_read.readframes"),
        (76, 1, "CAP009", "wave.Wave_write.writeframes"),
        (78, 1, "CAP005", "asyncio.StreamWriter.write"),
        (80, 1, "CAP005", "asyncio.DatagramTransport.sendto"),
        (82, 1, "CAP010", "concurrent.futures.ProcessPoolExecutor.submit"),
        (84, 1, "CAP005", "asyncio.Server.serve_forever"),
        (86, 1, "CAP005", "asyncio.base_events.Server.close"),
        (88, 1, "CAP010", "asyncio.subprocess.Process.communicate"),
        (90, 1, "CAP010", "asyncio.SubprocessTransport.kill"),
        (92, 1, "CAP010", "asyncio.transports.SubprocessTransport.kill"),
        (94, 1, "CAP009", "os.DirEntry.stat"),
        (96, 1, "CAP009", "tempfile.TemporaryDirectory.cleanup"),
        (98, 1, "CAP009", "importlib.resources.abc.Traversable.read_bytes"),
        (100, 1, "CAP005", "asyncio.AbstractServer.close"),
        (102, 1, "CAP005", "asyncio.events.AbstractServer.serve_forever"),
        (110, 1, "CAP005", "asyncio.AbstractServer.close"),
    ),
    "try_prefixes.py.txt": (
        (10, 5, "CAP009", "pathlib.Path.read_text"),
        (17, 5, "CAP002", "random.Random without an explicit seed"),
        (27, 9, "CAP009", "pathlib.Path.read_text"),
        (36, 9, "CAP009", "pathlib.Path.read_text"),
    ),
    "uuid.py.txt": ((3, 14, "CAP003", "uuid.uuid4"),),
    "wall_clock.py.txt": (
        (3, 15, "CAP001", "datetime.datetime.now"),
        (4, 17, "CAP001", "datetime.datetime.now"),
    ),
}


def test_current_production_capabilities_have_no_ambient_effects() -> None:
    assert inspect_capability_dependencies(PACKAGE_ROOT) == ()


def test_typed_values_ports_seeded_randomness_and_explicit_time_are_allowed(
    tmp_path: Path,
) -> None:
    package_root = _fixture_package(tmp_path)
    allowed = (
        "dataclass_match_options.py.txt",
        "dependency_injection.py.txt",
        "logging.py.txt",
        "nullable_fallback.py.txt",
        "path_values.py.txt",
        "seeded_random.py.txt",
        "time_inputs.py.txt",
        "typed_values.py.txt",
    )
    for fixture_name in allowed:
        destination = package_root / "domain" / fixture_name.removesuffix(".txt")
        destination.write_text(
            (FIXTURE_ROOT / "allowed" / fixture_name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    assert inspect_capability_dependencies(package_root) == ()


@pytest.mark.parametrize(("fixture_name", "expected"), sorted(EXPECTED_DIAGNOSTICS.items()))
def test_each_prohibited_syntax_is_rejected(
    tmp_path: Path,
    fixture_name: str,
    expected: tuple[ExpectedDiagnostic, ...],
) -> None:
    package_root = _fixture_package(tmp_path)
    source_file = package_root / "domain" / "violation.py"
    source_file.write_text(
        (FIXTURE_ROOT / "denied" / fixture_name).read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert {violation.path for violation in violations} == {"domain/violation.py"}
    assert (
        tuple(
            (violation.line, violation.column, violation.rule_id, violation.subject)
            for violation in violations
        )
        == expected
    )


def test_direct_aliases_and_local_reexports_cannot_hide_effects(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "effects.py").write_text(
        "from datetime import datetime\nimport os\nfrom pathlib import Path\nlookup = os.getenv\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "consumer.py").write_text(
        "from . import effects\n"
        "from .effects import Path, lookup\n"
        "lookup('TOKEN')\n"
        "Path('state').read_text()\n"
        "effects.datetime.now()\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert {violation.rule_id for violation in violations} == {"CAP001", "CAP004", "CAP009"}
    assert "domain/consumer.py" in {violation.path for violation in violations}


def test_comprehension_assignment_expression_reexports_retain_effect_identity(
    tmp_path: Path,
) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "effects.py").write_text(
        "from pathlib import Path\n"
        "ROOT_RESULT: object = 'safe'\n"
        "[(ROOT_RESULT := root) for root in [Path('state')]]\n"
        "ROOT_FILTER: object = 'safe'\n"
        "[None for root in [Path('state')] if (ROOT_FILTER := root)]\n"
        "ROOT_GENERATOR: object = 'safe'\n"
        "list((ROOT_GENERATOR := root) for root in [Path('state')])\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "consumer.py").write_text(
        "from .effects import ROOT_FILTER, ROOT_GENERATOR, ROOT_RESULT\n"
        "ROOT_FILTER.read_text()\n"
        "ROOT_GENERATOR.read_text()\n"
        "ROOT_RESULT.read_text()\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert [(item.path, item.line, item.subject) for item in violations] == [
        ("domain/consumer.py", 2, "pathlib.Path.read_text"),
        ("domain/consumer.py", 3, "pathlib.Path.read_text"),
        ("domain/consumer.py", 4, "pathlib.Path.read_text"),
    ]


def test_cross_module_class_field_patterns_retain_effect_identity(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "effects.py").write_text(
        "import gzip\n"
        "import shelve\n"
        "class Store:\n"
        "    __match_args__ = ('archive',)\n"
        "    archive: gzip.GzipFile\n"
        "    shelf: shelve.Shelf[str]\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "consumer.py").write_text(
        "from .effects import Store\n"
        "def inspect(store: Store) -> None:\n"
        "    match store:\n"
        "        case Store((head, *tail)):\n"
        "            pass\n"
        "    match store:\n"
        "        case Store(shelf={'decision': value}):\n"
        "            pass\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert [(item.path, item.line, item.column, item.subject) for item in violations] == [
        ("domain/consumer.py", 4, 20, "gzip.GzipFile.__iter__"),
        ("domain/consumer.py", 7, 26, "shelve.Shelf.__getitem__"),
    ]


def test_cross_module_dataclass_patterns_retain_effect_identity(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "effects.py").write_text(
        "from dataclasses import dataclass\n"
        "import shelve\n"
        "@dataclass\n"
        "class Store:\n"
        "    shelf: shelve.Shelf[str]\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "consumer.py").write_text(
        "from .effects import Store\n"
        "def inspect(store: Store) -> None:\n"
        "    match store:\n"
        "        case Store({'decision': value}):\n"
        "            pass\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert [(item.path, item.line, item.column, item.subject) for item in violations] == [
        ("domain/consumer.py", 4, 20, "shelve.Shelf.__getitem__")
    ]


def test_conditional_local_reexports_retain_typed_effect_identity(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "effects.py").write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pathlib import Path\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "consumer.py").write_text(
        "from __future__ import annotations\n"
        "from .effects import Path\n"
        "def read(path: Path) -> str:\n"
        "    return path.read_text()\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert [(item.path, item.line, item.subject) for item in violations] == [
        ("domain/consumer.py", 4, "pathlib.Path.read_text")
    ]


def test_conditional_exported_classes_retain_typed_fields(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "provider.py").write_text(
        "from pathlib import Path\n"
        "from . import values\n"
        "if True:\n"
        "    class Store:\n"
        "        root: Path\n"
        "    class AssignedStore:\n"
        "        if True:\n"
        "            root = values.make_path()\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "values.py").write_text(
        "from pathlib import Path\ndef make_path() -> Path:\n    return Path('state')\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "consumer.py").write_text(
        "from __future__ import annotations\n"
        "from . import provider\n"
        "def read(store: provider.Store) -> str:\n"
        "    return store.root.read_text()\n"
        "def read_assigned(store: provider.AssignedStore) -> str:\n"
        "    return store.root.read_text()\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    actual = [
        (item.path, item.line, item.column, item.rule_id, item.subject) for item in violations
    ]

    assert actual == [
        ("domain/consumer.py", 4, 12, "CAP009", "pathlib.Path.read_text"),
        ("domain/consumer.py", 6, 12, "CAP009", "pathlib.Path.read_text"),
    ]


def test_exported_class_fields_retain_abrupt_control_flow_bindings(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "provider.py").write_text(
        "from pathlib import Path\n"
        "class HandlerStore:\n"
        "    try:\n"
        "        if True:\n"
        "            candidate = Path('state')\n"
        "            raise RuntimeError\n"
        "            candidate = object()\n"
        "    except RuntimeError:\n"
        "        root = candidate\n"
        "class FinallyStore:\n"
        "    try:\n"
        "        candidate = Path('state')\n"
        "        raise RuntimeError\n"
        "        candidate = object()\n"
        "    finally:\n"
        "        root = candidate\n"
        "class BreakStore:\n"
        "    candidate = object()\n"
        "    for item in (Path('state'),):\n"
        "        candidate = item\n"
        "        break\n"
        "        candidate = object()\n"
        "    root = candidate\n"
        "class MethodHandlerStore:\n"
        "    def __init__(self) -> None:\n"
        "        try:\n"
        "            if True:\n"
        "                candidate = Path('state')\n"
        "                raise RuntimeError\n"
        "                candidate = object()\n"
        "        except RuntimeError:\n"
        "            self.root = candidate\n"
        "class MethodFinallyStore:\n"
        "    def __init__(self) -> None:\n"
        "        try:\n"
        "            candidate = Path('state')\n"
        "            raise RuntimeError\n"
        "            candidate = object()\n"
        "        finally:\n"
        "            self.root = candidate\n"
        "class MethodContinueStore:\n"
        "    def __init__(self) -> None:\n"
        "        candidate = object()\n"
        "        for item in (Path('state'),):\n"
        "            candidate = item\n"
        "            continue\n"
        "            candidate = object()\n"
        "        self.root = candidate\n"
        "class MethodReturnStore:\n"
        "    def __init__(self) -> None:\n"
        "        candidate = object()\n"
        "        for item in (Path('state'),):\n"
        "            try:\n"
        "                return (candidate := item)\n"
        "            finally:\n"
        "                continue\n"
        "        self.root = candidate\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "consumer.py").write_text(
        "from . import provider\n"
        "def read_handler(store: provider.HandlerStore) -> str:\n"
        "    return store.root.read_text()\n"
        "def read_finally(store: provider.FinallyStore) -> str:\n"
        "    return store.root.read_text()\n"
        "def read_break(store: provider.BreakStore) -> str:\n"
        "    return store.root.read_text()\n"
        "def read_method_handler(store: provider.MethodHandlerStore) -> str:\n"
        "    return store.root.read_text()\n"
        "def read_method_finally(store: provider.MethodFinallyStore) -> str:\n"
        "    return store.root.read_text()\n"
        "def read_method_continue(store: provider.MethodContinueStore) -> str:\n"
        "    return store.root.read_text()\n"
        "def read_method_return(store: provider.MethodReturnStore) -> str:\n"
        "    return store.root.read_text()\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert [
        (item.path, item.line, item.column, item.rule_id, item.subject) for item in violations
    ] == [
        ("domain/consumer.py", line, 12, "CAP009", "pathlib.Path.read_text")
        for line in (3, 5, 7, 9, 11, 13, 15)
    ]


def test_imported_random_subclasses_retain_seed_requirements(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "provider.py").write_text(
        "import random\nclass Generator(random.Random):\n    pass\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "consumer.py").write_text(
        "from . import provider\n"
        "from .provider import Generator\n"
        "from .derived import DerivedGenerator\n"
        "Generator()\n"
        "provider.Generator()\n"
        "DerivedGenerator()\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "derived.py").write_text(
        "from . import provider\nclass DerivedGenerator(provider.Generator):\n    pass\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert [
        (item.path, item.line, item.column, item.rule_id, item.subject) for item in violations
    ] == [
        (
            "domain/consumer.py",
            4,
            1,
            "CAP002",
            "random.Random subclass without an explicit seed",
        ),
        (
            "domain/consumer.py",
            5,
            1,
            "CAP002",
            "random.Random subclass without an explicit seed",
        ),
        (
            "domain/consumer.py",
            6,
            1,
            "CAP002",
            "random.Random subclass without an explicit seed",
        ),
    ]


def test_module_object_assignment_reexports_retain_effect_identity(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "provider.py").write_text(
        "import asyncio\n"
        "import random\n"
        "from datetime import datetime\n"
        "from pathlib import Path\n"
        "class Generator(random.Random):\n    pass\n"
        "class StoredPath(Path):\n    pass\n"
        "class Loop(asyncio.SelectorEventLoop):\n    pass\n"
        "class Clock(datetime):\n    pass\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "reexports.py").write_text(
        "from . import provider\n"
        "*generator_values, = (provider.Generator,)\n"
        "Generator, = generator_values\n"
        "*path_values, = (provider.StoredPath,)\n"
        "StoredPath, = path_values\n"
        "*loop_values, = (provider.Loop,)\n"
        "Loop, = loop_values\n"
        "*clock_values, = (provider.Clock,)\n"
        "Clock, = clock_values\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "consumer.py").write_text(
        "from .reexports import Clock, Generator, Loop, StoredPath\n"
        "Generator()\n"
        "StoredPath('state').read_text()\n"
        "Loop().time()\n"
        "Loop().create_connection(None, 'localhost', 80)\n"
        "Loop().subprocess_exec(None, 'true')\n"
        "Clock.now()\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert [
        (item.path, item.line, item.column, item.rule_id, item.subject) for item in violations
    ] == [
        (
            "domain/consumer.py",
            2,
            1,
            "CAP002",
            "random.Random subclass without an explicit seed",
        ),
        ("domain/consumer.py", 3, 1, "CAP009", "pathlib.Path.read_text"),
        ("domain/consumer.py", 4, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        (
            "domain/consumer.py",
            5,
            1,
            "CAP005",
            "asyncio.AbstractEventLoop.create_connection",
        ),
        (
            "domain/consumer.py",
            6,
            1,
            "CAP010",
            "asyncio.AbstractEventLoop.subprocess_exec",
        ),
        ("domain/consumer.py", 7, 1, "CAP001", "datetime.datetime.now"),
    ]


def test_summary_bindings_retain_module_and_class_effect_identity(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "provider.py").write_text(
        "import asyncio\n"
        "import random\n"
        "from pathlib import Path\n"
        "OUT = (Generator := random.Random)\n"
        "class Store:\n"
        "    root = (alias := Path('state'))\n"
        "class RootStore:\n"
        "    root = Path('state')\n"
        "ROOT = RootStore.root\n"
        "with asyncio.Runner() as runner:\n"
        "    LOOP = runner.get_loop()\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "consumer.py").write_text(
        "from .provider import Generator, LOOP, ROOT, Store\n"
        "Generator()\n"
        "LOOP.time()\n"
        "ROOT.read_text()\n"
        "Store().alias.read_text()\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert [
        (item.path, item.line, item.column, item.rule_id, item.subject) for item in violations
    ] == [
        (
            "domain/consumer.py",
            2,
            1,
            "CAP002",
            "random.Random without an explicit seed",
        ),
        ("domain/consumer.py", 3, 1, "CAP001", "asyncio.AbstractEventLoop.time"),
        ("domain/consumer.py", 4, 1, "CAP009", "pathlib.Path.read_text"),
        ("domain/consumer.py", 5, 1, "CAP009", "pathlib.Path.read_text"),
    ]


def test_try_summaries_preserve_ordered_module_and_class_bindings(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "provider.py").write_text(
        "from pathlib import Path\n"
        "try:\n"
        "    ROOT = Path('state')\n"
        "    raise RuntimeError\n"
        "    ROOT = object()\n"
        "except RuntimeError:\n"
        "    ALIAS = ROOT\n"
        "finally:\n"
        "    pass\n"
        "class Store:\n"
        "    try:\n"
        "        base = Path('state')\n"
        "        raise RuntimeError\n"
        "        base = object()\n"
        "    except RuntimeError:\n"
        "        root = base\n"
        "    finally:\n"
        "        pass\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "consumer.py").write_text(
        "from .provider import ALIAS, Store\nALIAS.read_text()\nStore.root.read_text()\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert [
        (item.path, item.line, item.column, item.rule_id, item.subject) for item in violations
    ] == [
        ("domain/consumer.py", 2, 1, "CAP009", "pathlib.Path.read_text"),
        ("domain/consumer.py", 3, 1, "CAP009", "pathlib.Path.read_text"),
    ]


def test_path_annotations_and_fields_expose_filesystem_calls(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "storage.py").write_text(
        "from pathlib import Path\n"
        "def read(path: Path) -> str:\n"
        "    return path.read_text()\n"
        "class Store:\n"
        "    def __init__(self, root: Path) -> None:\n"
        "        self.root: Path = root\n"
        "    def read(self) -> str:\n"
        "        return self.root.read_text()\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert [(item.line, item.subject) for item in violations] == [
        (3, "pathlib.Path.read_text"),
        (8, "pathlib.Path.read_text"),
    ]


def test_explicit_timezone_and_seed_fallbacks_remain_deterministic(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "deterministic.py").write_text(
        "import random\n"
        "from datetime import UTC, datetime\n"
        "def build(seed: int | None, value: float) -> tuple[object, datetime]:\n"
        "    generator = random.Random(seed if seed is not None else 0)\n"
        "    return generator, datetime.fromtimestamp(value, UTC)\n",
        encoding="utf-8",
    )

    assert inspect_capability_dependencies(package_root) == ()


def test_timezone_aware_guards_allow_explicit_conversion(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "deterministic.py").write_text(
        "from datetime import UTC, datetime\n"
        "def normalize(value: datetime) -> datetime:\n"
        "    if value.utcoffset() is None:\n"
        "        raise ValueError\n"
        "    return value.astimezone(UTC)\n"
        "def normalize_branch(value: datetime) -> datetime:\n"
        "    if value.utcoffset() is not None:\n"
        "        return value.astimezone(UTC)\n"
        "    raise ValueError\n",
        encoding="utf-8",
    )

    assert inspect_capability_dependencies(package_root) == ()


def test_fallthrough_timezone_guard_remains_host_local(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "unsafe.py").write_text(
        "from datetime import UTC, datetime\n"
        "def normalize(value: datetime) -> datetime:\n"
        "    if value.utcoffset() is None:\n"
        "        pass\n"
        "    return value.astimezone(UTC)\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert [(item.line, item.rule_id, item.subject) for item in violations] == [
        (5, "CAP004", "datetime.datetime.astimezone")
    ]


def test_module_qualified_nullable_timezones_remain_host_local(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "values.py").write_text(
        "def missing_timezone() -> None:\n    return None\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "provider.py").write_text(
        "from . import values\nMISSING_TIMEZONE = values.missing_timezone()\n",
        encoding="utf-8",
    )
    (package_root / "domain" / "consumer.py").write_text(
        "from datetime import UTC, date, datetime, time\n"
        "from . import provider\n"
        "datetime(2026, 1, 1, tzinfo=provider.MISSING_TIMEZONE).timestamp()\n"
        "datetime.combine(date(2026, 1, 1), time(), tzinfo=provider.MISSING_TIMEZONE).timestamp()\n"
        "datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=provider.MISSING_TIMEZONE).timestamp()\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert [
        (item.path, item.line, item.column, item.rule_id, item.subject) for item in violations
    ] == [
        ("domain/consumer.py", 3, 1, "CAP004", "datetime.datetime.timestamp"),
        ("domain/consumer.py", 4, 1, "CAP004", "datetime.datetime.timestamp"),
        ("domain/consumer.py", 5, 1, "CAP004", "datetime.datetime.timestamp"),
    ]


@pytest.mark.parametrize("zone", ["adapters", "entrypoints"])
def test_effect_zones_retain_location_bound_permissions(tmp_path: Path, zone: str) -> None:
    package_root = _fixture_package(tmp_path)
    source_file = package_root / zone / "effects.py"
    source_file.parent.mkdir()
    source_file.write_text(
        "import os\nimport sqlite3\nfrom pathlib import Path\n"
        "os.getenv('TOKEN')\nPath('state').write_text('x')\nsqlite3.connect('state')\n",
        encoding="utf-8",
    )

    assert inspect_capability_dependencies(package_root) == ()


def test_new_top_level_capability_is_protected_automatically(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    source_file = package_root / "future_capability" / "unsafe.py"
    source_file.parent.mkdir()
    source_file.write_text("from time import time\nobserved_at = time()\n", encoding="utf-8")

    violations = inspect_capability_dependencies(package_root)

    assert {violation.rule_id for violation in violations} == {"CAP001"}
    assert {violation.path for violation in violations} == {"future_capability/unsafe.py"}


@pytest.mark.parametrize(
    ("source", "rule_id"),
    [
        ("from os import *\n", "CAP004"),
        ("from pathlib import *\n", "CAP009"),
        ("from random import *\n", "CAP002"),
        ("from time import *\n", "CAP001"),
    ],
)
def test_effect_bearing_wildcard_imports_fail_closed(
    tmp_path: Path,
    source: str,
    rule_id: str,
) -> None:
    package_root = _fixture_package(tmp_path)
    (package_root / "domain" / "wildcard.py").write_text(source, encoding="utf-8")

    violations = inspect_capability_dependencies(package_root)

    assert {violation.rule_id for violation in violations} == {rule_id}


def test_diagnostics_are_bounded_and_do_not_echo_hostile_values(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    hostile_value = "HOSTILE_SECRET_VALUE"
    (package_root / "domain" / "unsafe.py").write_text(
        f"import os\nos.getenv('{hostile_value}')\n",
        encoding="utf-8",
    )

    diagnostic = inspect_capability_dependencies(package_root)[0].format()

    assert diagnostic.startswith("domain/unsafe.py:2:1: CAP004")
    assert "os.getenv" in diagnostic
    assert GOVERNING_RULE in diagnostic
    assert hostile_value not in diagnostic
    assert str(tmp_path) not in diagnostic


def test_invalid_source_fails_closed_without_parser_or_source_text(tmp_path: Path) -> None:
    package_root = _fixture_package(tmp_path)
    hostile_value = "HOSTILE_SOURCE_VALUE"
    (package_root / "domain" / "invalid.py").write_text(
        f"if {hostile_value}\n",
        encoding="utf-8",
    )

    violations = inspect_capability_dependencies(package_root)

    assert len(violations) == 1
    assert violations[0].rule_id == "CAP000"
    assert hostile_value not in violations[0].format()


def test_cli_reports_successful_protected_file_count(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package_root = tmp_path / "src" / "agentic_investment_os"
    (package_root / "domain").mkdir(parents=True)
    (package_root / "domain" / "safe.py").write_text("value = 1\n", encoding="utf-8")

    result = main(["--root", str(tmp_path)])

    assert result == 0
    assert capsys.readouterr().out == "validated 1 protected production files\n"


def test_cli_fails_when_the_production_package_is_unavailable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(["--root", str(tmp_path)])

    assert result == UNAVAILABLE_EXIT_CODE
    assert capsys.readouterr().err == "capability package is unavailable\n"


def _fixture_package(tmp_path: Path) -> Path:
    package_root = tmp_path / "agentic_investment_os"
    (package_root / "domain").mkdir(parents=True)
    (package_root / "__init__.py").touch()
    (package_root / "domain" / "__init__.py").touch()
    return package_root

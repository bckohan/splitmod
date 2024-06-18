r"""

 _______  _______  ___      ___   _______  __   __  _______  ______
|       ||       ||   |    |   | |       ||  |_|  ||       ||      |
|  _____||    _  ||   |    |   | |_     _||       ||   _   ||  _    |
| |_____ |   |_| ||   |    |   |   |   |  |       ||  | |  || | |   |
|_____  ||    ___||   |___ |   |   |   |  |       ||  |_|  || |_|   |
 _____| ||   |    |       ||   |   |   |  | ||_|| ||       ||       |
|_______||___|    |_______||___|   |___|  |_|   |_||_______||______|


Split a single python module across multiple files. Each file will be executed
into the root module's scope when it is included. This is useful for breaking apart
large python based settings files like those used in frameworks like Django.


TODO Using the importlib machinery initially seemed like a good approach, but the challenge
with this is when importing package hierarchies the loaders of the parent packages are
used, working around this is both hacky and I haven't been able to get it to work.

Tableing this to have a bit of a rethink.
"""

import sys
import typing as t
from contextlib import contextmanager
from functools import partial
from importlib import import_module
from importlib.abc import Loader, MetaPathFinder, PathEntryFinder
from importlib.machinery import (
    FileFinder,
    PathFinder,
    SourceFileLoader,
    SourcelessFileLoader,
)
from os import PathLike
from pathlib import Path

VERSION = (1, 0, 0)

__title__ = "SplitMod"
__version__ = ".".join(str(i) for i in VERSION)
__author__ = "Brian Kohan"
__license__ = "MIT"
__copyright__ = "Copyright 2024 Brian Kohan"

__all__ = [
    "include",
    "optional",
    "set_default",
    "is_defined",
    "get",
]


class _NotGiven:
    pass


class SplitModuleLoader(Loader):
    scope: t.Dict[str, t.Any]
    is_loading: bool = False
    path: t.Optional[str] = None
    fullname: t.Optional[str] = None

    def __init__(self, scope: t.Dict[str, t.Any]):
        self.scope = scope

    def create_module(self, spec):
        return None  # do normal module creation logic

    def exec_module(self, module):
        code_file = self.path or getattr(module.__spec__, "origin", None)
        assert code_file
        self.is_loading = True
        stack = self.scope.get("__included_files__", [])
        stack.append(Path(code_file))
        with open(code_file, "r") as file:
            exec(file.read(), self.scope)
        self.is_loading = False
        self.scope["__included_files__"] = stack

    def __call__(self, fullname: str, path: str):
        """
        When used as a FileFinder loader we make this object callable to spoof
        it as a class. The FileFinder passes these two init parameters to each
        loader class.
        """
        self.fullname = fullname
        self.path = path
        return self


class SplitFinder(MetaPathFinder):
    split_loader: SplitModuleLoader

    def __init__(self, loader: SplitModuleLoader, *args, **kwargs):
        self.split_loader = loader
        super().__init__(*args, **kwargs)

    def __enter__(self):
        sys.meta_path.insert(0, self)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        sys.meta_path.remove(self)


class SplitFileFinder(SplitFinder):
    def __init__(self, scope: t.Dict[str, t.Any], path: PathLike, suffix: str):
        loader = SplitModuleLoader(scope)
        self.file_finder = FileFinder(
            str(path),
            (  # type: ignore
                loader,  # pyright: ignore[reportArgumentType]
                [
                    suffix,
                ],
            ),
        )
        super().__init__(loader)

    def find_spec(self, fullname, path=None, target=None):
        return self.file_finder.find_spec(fullname, target=target)


class SplitModuleFinder(SplitFinder, PathFinder):  # pyright: ignore[reportIncompatibleMethodOverride]
    def __init__(self, scope: t.Dict[str, t.Any]):
        super().__init__(SplitModuleLoader(scope))

    def find_spec(self, fullname, path, target=None):  # pyright: ignore[reportIncompatibleMethodOverride]
        if self.split_loader.is_loading:
            return None
        spec = super().find_spec(fullname, path, target)
        if spec:
            spec.loader = self.split_loader
        return spec


def set_default(var_name, default, set_if_none=True):
    """
    Set the value of the given variable in the calling scope to a default value if it has been not been previously
    defined.

    :param var_name: The name of the variable to set in the calling scope.
    :param default: The value to set the variable to if its undefined
    :param set_if_none: Treat the variable as undefined if it is None, default: True
    :return: the variable that was set
    """
    scope = sys._getframe(1).f_globals
    if var_name not in scope or set_if_none and scope[var_name] is None:
        scope[var_name] = default
    return scope[var_name]


def is_defined(var_name):
    """
    Returns true if the given variable has been defined in the calling scope.

    :param var_name: The name of the variable to check for
    :return: True if the variable is defined, false otherwise
    """
    return var_name in sys._getframe(1).f_globals


def get(var_name, default=_NotGiven):
    """
    Returns the value of the setting if it exists, if it does not exist the value
    given to default is returned. If no default value is given and the setting
    does not exist a NameError is raised

    :param var_name: The name of the variable to check for
    :param default: The default value to return if the variable does not exist
    :raises NameError: if the name is undefined and no default is given.
    """
    value = sys._getframe(1).f_globals.get(var_name, default)
    if value is _NotGiven:
        raise NameError(f"{var_name} setting variable is not defined.", name=var_name)
    return value


@contextmanager
def _load_file_path_as_module(root: PathLike):
    root = Path(root)

    def finder(path: str) -> PathEntryFinder:
        pth = Path(path)
        if any(root == parent for parent in pth.parents) or root == pth:
            return FileFinder(
                path, (SourceFileLoader, [".py"]), (SourcelessFileLoader, [".pyc"])
            )
        raise ImportError()

    sys.path_hooks.insert(0, finder)
    sys.path.append(str(root))
    sys.path_importer_cache.clear()
    yield finder
    sys.path_hooks.remove(finder)
    sys.path.remove(str(root))
    sys.path_importer_cache.clear()


def include(
    resource: t.Union[str, PathLike],
    scope: t.Optional[t.Dict[str, t.Any]] = None,
    optional: bool = False,
):
    """
    Include the given module or python file at the current location. This works as if you had
    cut and paste the contents of the referenced file where you make the include call.

    :param resource: An import path or pathlike object
    :param scope: The outer scope that the resource path should be execute in. By default the
        scope from the calling context will be used which is most likely what you want.
    :param optional: If true, if the file does not exist no error will be thrown.
    """
    mod = None
    loader = None
    try:
        frame = sys._getframe(1)
        scope = scope or frame.f_globals

        def use_loaded(mod_path) -> bool:
            module = sys.modules.get(mod_path, None)
            if module:
                scope.update(vars(module))
                return True
            return False

        if isinstance(resource, Path) or (
            isinstance(resource, str) and "/" in resource
        ):
            resource = Path(resource)
            if use_loaded(".".join(resource.parts)):
                return
            root = Path(frame.f_code.co_filename).parent
            parent = ""
            if len(resource.parts) > 1:
                # use the normal import chain for all parents incase they havent
                # been loaded yet
                parent = ".".join(resource.parts[0:-1]).lstrip(".")
                with _load_file_path_as_module(root):
                    mod = import_module(parent)
                    assert mod.__spec__
                    loader = mod.__spec__.loader
                    mod.__spec__.loader = SplitModuleLoader(scope)
                    mod.__loader__ = mod.__spec__.loader
                    sys.modules[parent] = mod
            # import ipdb
            # ipdb.set_trace()
            with SplitFileFinder(scope, root, resource.suffix):
                import_module(f"{parent}.{resource.stem}".lstrip("."))
        else:
            resource = str(resource)
            if use_loaded(resource):
                return
            if "." in resource:
                # use the normal import chain for all parents incase they havent
                # been loaded yet
                parent = ".".join(resource.split(".")[0:-1])
                mod = import_module(parent)
                assert mod.__spec__
                loader = mod.__spec__.loader
                mod.__spec__.loader = SplitModuleLoader(scope)
                mod.__loader__ = mod.__spec__.loader
                sys.modules[parent] = mod
            with SplitModuleFinder(scope):
                import_module(str(resource))

    except Exception:
        if optional:
            return
        raise
    finally:
        if mod and loader:
            assert mod.__spec__
            mod.__spec__.loader = loader


optional = partial(include, optional=True)
"""
A wrapper for include() that sets optional to True by default.
"""

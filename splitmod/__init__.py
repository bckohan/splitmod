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
"""

import sys
import typing as t
from functools import partial
from importlib import import_module
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import FileFinder, PathFinder
from os import PathLike
from pathlib import Path

VERSION = (1, 0, 0)

__title__ = "SplitMod"
__version__ = ".".join(str(i) for i in VERSION)
__author__ = "Brian Kohan"
__license__ = "MIT"
__copyright__ = "Copyright 2024 Brian Kohan"

__all__ = [
    "INCLUDE_STACK",
    "include",
    "optional",
    "set_default",
    "is_defined",
    "get",
]


class _NotGiven:
    pass


INCLUDE_STACK: t.List[Path] = []


class SplitModuleLoader(Loader):
    # the settings scope
    settings: t.Dict[str, t.Any]
    is_loading: bool = False
    path: t.Optional[str] = None
    fullname: t.Optional[str] = None

    def __init__(self, settings):
        self.settings = settings

    def create_module(self, spec):
        return None  # do normal module creation logic

    def exec_module(self, module):
        global INCLUDE_STACK
        code_file = self.path or getattr(module.__spec__, "origin", None)
        assert code_file
        INCLUDE_STACK.append(Path(code_file))
        self.is_loading = True
        with open(code_file, "r") as file:
            exec(file.read(), self.settings)
        self.is_loading = False

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


class SplitFileFinder(SplitFinder, FileFinder):
    def __init__(self, scope: t.Dict[str, t.Any], path: PathLike, suffix: str):
        super().__init__(scope, str(path), (SplitModuleLoader(scope), (suffix,)))  # type: ignore

    def find_spec(self, fullname, target=None):  # pyright: ignore[reportIncompatibleMethodOverride]
        return super().find_spec(fullname, target)


class SplitModuleFinder(SplitFinder, PathFinder):  # pyright: ignore[reportIncompatibleMethodOverride]
    def __init__(self, scope: t.Dict[str, t.Any]):
        super().__init__(SplitModuleLoader(scope))

    def find_spec(self, fullname, path, target=None):  # pyright: ignore[reportIncompatibleMethodOverride]
        if self.split_loader.is_loading:
            return None
        spec = super().find_spec(fullname, path, target)  # pyright: ignore[reportIncompatibleMethodOverride]
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
    try:
        frame = sys._getframe(1)
        scope = scope or frame.f_globals

        if isinstance(resource, Path) or (
            isinstance(resource, str) and "/" in resource
        ):
            resource = Path(resource)

            with SplitFileFinder(
                scope, Path(frame.f_code.co_filename).parent, resource.suffix
            ):
                import_module(
                    f'{".".join(resource.parts[0:-1])}.{resource.stem}'.lstrip(".")
                )
        else:
            with SplitModuleFinder(scope):
                import_module(str(resource))

    except Exception:
        if optional:
            return
        raise


optional = partial(include, optional=True)
"""
A wrapper for include() that sets optional to True by default.
"""

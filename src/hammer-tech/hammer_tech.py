#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#  hammer_tech.py
#  Python interface to the hammer technology abstraction.
#
#  See LICENSE for licence details.

import json
import os
import subprocess
from abc import ABCMeta, abstractmethod
from typing import Any, Callable, Iterable, List, NamedTuple, Optional, Tuple, Dict
from decimal import Decimal

import hammer_config
import python_jsonschema_objects  # type: ignore

from hammer_config import load_yaml
from hammer_logging import HammerVLSILoggingContext
from hammer_utils import (LEFUtils, add_lists, deeplist, get_or_else,
                          in_place_unique, optional_map, reduce_list_str,
                          reduce_named, coerce_to_grid)

from library_filter import LibraryFilter
from filters import LibraryFilterHolder
from stackup import RoutingDirection, WidthSpacingTuple, Metal, Stackup

# Holds the list of pre-implemented filters.
# Access it like hammer_tech.filters.lef_filter
filters = LibraryFilterHolder()

builder = python_jsonschema_objects.ObjectBuilder(json.loads(open(os.path.dirname(__file__) + "/schema.json").read()))
ns = builder.build_classes()

# Pull definitions from the autoconstructed classes.
TechJSON = ns.Techjson
# Semiconductor IP library
Library = ns.Library


class LibraryPrefix(metaclass=ABCMeta):
    """
    Base type for all library path prefixes.
    """

    @property
    @abstractmethod
    def prefix(self) -> str:
        """
        Get the prefix that this LibraryPrefix instance provides.
        For example, if this is a path prefix for "myprefix" -> "/usr/share/myprefix", then
        this method returns "myprefix".
        :return: Prefix of this LibraryPrefix.
        """
        pass

    @abstractmethod
    def prepend(self, rest_of_path: str) -> str:
        """
        Prepend the path held by this LibraryPrefix to the given rest_of_path.
        The exact implementation of this depends on the subclass. For example,
        a path prefix may just append the path it holds, while a variable
        prefix might do some lookups.
        :param rest_of_path: Rest of the path
        :return: Path held by this prefix prepended to rest_of_path.
        """
        pass


# Internal backend of PathPrefix. Do not use.
_PathPrefixInternal = NamedTuple('PathPrefix', [
    ('prefix', str),
    ('path', str)
])


class PathPrefix(LibraryPrefix):
    """
    # Struct that holds a path-based prefix.
    """
    __slots__ = ('internal',)

    def __init__(self, prefix: str, path: str) -> None:
        """
        Initialize a new PathPrefix.
        e.g. a PathPrefix might map 'mylib' to '/usr/lib/mylib'.
        :param prefix: Prefix to hold e.g. 'mylib'
        :param path: Path to map this prefix to - e.g. '/usr/lib/mylib'.
        """
        self.internal = _PathPrefixInternal(
            prefix=str(prefix),
            path=str(path)
        )

    def __eq__(self, other) -> bool:
        return self.internal == other.internal

    @property
    def prefix(self) -> str:
        return self.internal.prefix

    @property
    def path(self) -> str:
        return self.internal.path

    def to_setting(self) -> dict:
        return {
            "prefix": self.prefix,
            "path": self.path
        }

    @staticmethod
    def from_setting(d: dict) -> "PathPrefix":
        return PathPrefix(
            prefix=str(d["prefix"]),
            path=str(d["path"])
        )

    def prepend(self, rest_of_path: str) -> str:
        return os.path.join(self.path, rest_of_path)


def _add_extra_prefixes() -> None:
    # Add extra_prefixes to Library.
    # Monkey-patch over the autogenerated classes for now.
    # See https://github.com/ucb-bar/hammer/issues/165
    # https://stackoverflow.com/a/36158137

    # Define getters and setters
    def get_extra_prefixes(self: Library) -> List[LibraryPrefix]:
        internal_list = getattr(self, "__donttouch_extra_prefixes", [])
        assert isinstance(internal_list, list)
        return deeplist(internal_list)

    def set_extra_prefixes(self: Library, value: List[LibraryPrefix]) -> None:
        assert isinstance(value, list)
        setattr(self, "__donttouch_extra_prefixes", deeplist(value))

    # Set them in the class
    setattr(Library, 'extra_prefixes', property(get_extra_prefixes, set_extra_prefixes))

    # Autogenerated classes override __setattr__ which prevents the setter above
    # from working, so we need to special case setattr...
    # Yes, this is incredibly ugly, and will be replaced when autogenerated classes are gone.

    # Keep a reference to the old __setattr__ (which we will be wrapping)
    __old_setattr = Library.__setattr__

    # Define a new __setattr__ that calls our setter if users try to set our new property.
    def __new_setattr(self: Library, name: str, value: Any) -> None:
        if name == "extra_prefixes":
            set_extra_prefixes(self, value)
        else:
            __old_setattr(self, name, value)

    setattr(Library, "__setattr__", __new_setattr)


_add_extra_prefixes()


# TODO(edwardw): deprecate these functions once Library is no longer auto-generated.
def copy_library(lib: Library) -> Library:
    """Perform a deep copy of a Library."""
    return Library.from_json(lib.serialize())


def library_from_json(json: str) -> Library:
    """
    Creatre a library from a JSON string.
    :param json: JSON string.
    :return: hammer_tech library.
    """
    return Library.from_json(json)


# Struct that holds an extra library and possible prefix.
class ExtraLibrary(NamedTuple('ExtraLibrary', [
    ('prefix', Optional[PathPrefix]),
    ('library', Library)
])):
    __slots__ = ()

    def to_setting(self) -> dict:
        raise NotImplementedError("No clean implementation for JSON export of library yet")

    @staticmethod
    def from_setting(d: dict) -> "ExtraLibrary":
        prefix = None
        if "prefix" in d:
            prefix = PathPrefix.from_setting(d["prefix"])
        return ExtraLibrary(
            prefix=prefix,
            library=HammerTechnology.parse_library(d["library"])
        )

    def store_into_library(self) -> Library:
        """
        Store the prefix into extra_prefixes of the library, and return a new copy.
        :return: A copy of the library in this ExtraPrefix with the prefix stored in extra_prefixes, if one exists.
        """
        lib_copied = copy_library(self.library)  # type: Library
        extra_prefixes = get_or_else(optional_map(self.prefix, lambda p: [p]), [])  # type: List[LibraryPrefix]
        lib_copied.extra_prefixes = extra_prefixes  # type: ignore
        return lib_copied

class Site(NamedTuple('Site', [
    ('name', str),
    ('x', Decimal),
    ('y', Decimal)
])):
    """
    A standard cell site, which is the minimum unit of x and y dimensions a standard cell can have.

    name: The name of this site (often something like "core") as defined in the tech and standard cell LEFs
    x: The x dimension
    y: The y dimension
    """
    __slots__ = ()

    @staticmethod
    def from_setting(grid_unit: Decimal, d: Dict[str, Any]) -> "Site":
        """
        Return a new Site

        :param grid_unit: The manufacturing grid unit in nm
        :param d: A dictionary with the keys "name", "x", and "y"
        :return: A Site
        """
        return Site(
            name=str(d["name"]),
            x=coerce_to_grid(d["x"], grid_unit),
            y=coerce_to_grid(d["y"], grid_unit)
        )



# Struct that holds information about the size of a macro.
# See defaults.yml.
class MacroSize(NamedTuple('MacroSize', [
    ('library', str),
    ('name', str),
    ('width', float),
    ('height', float)
])):
    __slots__ = ()

    def to_setting(self) -> dict:
        return {
            'library': self.library,
            'name': self.name,
            'width': str(self.width),
            'height': str(self.height)
        }

    @staticmethod
    def from_setting(d: dict) -> "MacroSize":
        return MacroSize(
            library=str(d['library']),
            name=str(d['name']),
            width=float(d['width']),
            height=float(d['height'])
        )


class HammerTechnology:
    # Properties.
    @property
    def cache_dir(self) -> str:
        """
        Get the location of a cache dir for this library.

        :return: Path to the location of the cache dir.
        """
        try:
            return self._cachedir
        except AttributeError:
            raise ValueError("Internal error: cache dir location not set by hammer-vlsi")

    @cache_dir.setter
    def cache_dir(self, value: str) -> None:
        """Set the directory as a persistent cache dir for this library."""
        self._cachedir = value  # type: str
        # Ensure the cache_dir exists.
        os.makedirs(value, exist_ok=True)

    # hammer-vlsi properties.
    # TODO: deduplicate/put these into an interface to share with HammerTool?
    @property
    def logger(self) -> HammerVLSILoggingContext:
        """Get the logger for this tool."""
        try:
            return self._logger
        except AttributeError:
            raise ValueError("Internal error: logger not set by hammer-vlsi")

    @logger.setter
    def logger(self, value: HammerVLSILoggingContext) -> None:
        """Set the logger for this tool."""
        self._logger = value  # type: HammerVLSILoggingContext

    # Methods.
    def __init__(self):
        """Don't call this directly. Use other constructors like load_from_dir()."""
        # Name of the technology
        self.name = ""  # type: str

        # Path to the technology folder
        self.path = ""  # type: str

        # Configuration
        self.config = None  # type: TechJSON

    @classmethod
    def load_from_dir(cls, technology_name: str, path: str) -> Optional["HammerTechnology"]:
        """Load a technology from a given folder.

        :param technology_name: Technology name (e.g. "saed32")
        :param path: Path to the technology folder (e.g. foo/bar/technology/saed32)
        :return: Loaded technology plugin or None if the folder did not have an appropriate tech.json/tech.yaml
        """
        json_path = os.path.join(path, "%s.tech.json" % technology_name)
        yaml_path = os.path.join(path, "%s.tech.yml" % technology_name)
        if os.path.exists(json_path):
            with open(json_path) as f:
                json_str = f.read()
                return HammerTechnology.load_from_json(technology_name, json_str, path)
        elif os.path.exists(yaml_path):
            with open(yaml_path) as f:
                yaml_str = f.read()
                return HammerTechnology.load_from_yaml(technology_name, yaml_str, path)
        else:
            return None

    @classmethod
    def load_from_json(cls, technology_name: str, json_str: str, path: str) -> "HammerTechnology":
        """Load a technology from a given folder.

        :param technology_name: Technology name (e.g. "saed32")
        :param json_str: JSON string to use as the technology JSON
        :param path: Path to set as the technology folder (e.g. foo/bar/technology/saed32)
        """

        tech = HammerTechnology()

        # Name of the technology
        tech.name = technology_name

        # Path to the technology folder
        tech.path = path

        # Configuration
        tech.config = TechJSON.from_json(json_str)

        return tech

    @classmethod
    def load_from_yaml(cls, technology_name: str, yaml_str: str, path: str) -> "HammerTechnology":
        """Load a technology from a given folder.

        :param technology_name: Technology name (e.g. "saed32")
        :param yaml_str: yaml string to use as the technology yaml
        :param path: Path to set as the technology folder (e.g. foo/bar/technology/saed32)
        """
        return HammerTechnology.load_from_json(technology_name, json.dumps(load_yaml(yaml_str)), path)

    def set_database(self, database: hammer_config.HammerDatabase) -> None:
        """Set the settings database for use by the tool."""
        self._database = database  # type: hammer_config.HammerDatabase

    def is_database_set(self) -> bool:
        """Return True if the settings database has been set for use by the tool."""
        return hasattr(self, "_database")

    def get_setting(self, key: str) -> Any:
        """Get a particular setting from the database.
        """
        try:
            return self._database.get(key)
        except AttributeError:
            raise ValueError("Internal error: no database set by hammer-vlsi")

    def has_setting(self, key: str) -> bool:
        """Check if a setting exists in the database.
        """
        return self._database.has_setting(key)

    def get_config(self) -> List[dict]:
        """Get the hammer configuration for this technology. Not to be confused with the ".tech.json" which self.config refers to."""
        return hammer_config.load_config_from_defaults(self.path)

    @property
    def dont_use_list(self) -> Optional[List[str]]:
        """
        Get the list of blacklisted ("don't use") cells.
        :return: List of "don't use" cells, or None if the technology does not define such a list.
        """
        dont_use_list_raw = self.config.dont_use_list  # type: Optional[List[str]]
        if dont_use_list_raw is None:
            return None
        else:
            # Work around the weird objects implemented by the jsonschema generator.
            dont_use_list = list(map(lambda x: str(x), list(dont_use_list_raw)))
            return dont_use_list

    @property
    def additional_drc_text(self) -> str:
        add_drc_text_raw = self.config.additional_drc_text
        if add_drc_text_raw is None:
            return ""
        else:
            return str(add_drc_text_raw)

    @property
    def additional_lvs_text(self) -> str:
        add_lvs_text_raw = self.config.additional_lvs_text
        if add_lvs_text_raw is None:
            return ""
        else:
            return str(add_lvs_text_raw)

    @property
    def extracted_tarballs_dir(self) -> str:
        """
        Return the path to a folder with extracted tarballs.
        If no pre-extracted dir is specified, then it will be under
        self.path.
        See defaults.yml.
        """
        tech_setting_key = "technology.{name}.extracted_tarballs_dir".format(name=self.name)
        if self.has_setting(tech_setting_key):
            tech_setting = self.get_setting(tech_setting_key)  # type: Optional[str]
            if tech_setting is not None:
                return tech_setting

        # No tech setting
        extracted_tarballs_dir_setting = self.get_setting("vlsi.technology.extracted_tarballs_dir")  # type: Optional[str]
        if extracted_tarballs_dir_setting is None:
            return os.path.join(self.cache_dir, "extracted")
        else:
            return extracted_tarballs_dir_setting

    @staticmethod
    def parse_library(lib: dict) -> Library:
        """
        Parse a given lib in dictionary form to a hammer_tech Library (IP library).
        :param lib: Library to parse, must be a dictionary
        :return: Parsed hammer_tech Library or exception.
        """
        if not isinstance(lib, dict):
            raise TypeError("lib must be a dict")

        # Convert the dict to JSON...
        return Library.from_json(json.dumps(lib))

    @property
    def tech_defined_libraries(self) -> List[Library]:
        """
        Get all technology-defined libraries from the config.
        :return: List of technology-defined libraries with any extra prefixes if present.
        """
        return list(self.config.libraries)

    def get_extra_macro_sizes(self) -> List[MacroSize]:
        """
        Get the list of extra macro sizes from the config.
        See vlsi.technology.extra_macro_sizes in defaults.yml.
        :return: List of extra macro sizes.
        """
        if not self.has_setting("vlsi.technology.extra_macro_sizes"):
            # If the key doesn't exist we can safely say there are none.
            return []

        extra_macro_sizes = self.get_setting("vlsi.technology.extra_macro_sizes")
        if not isinstance(extra_macro_sizes, list):
            raise ValueError("extra_macro_sizes was not a list")
        else:
            return list(map(MacroSize.from_setting, extra_macro_sizes))

    def get_tech_macro_sizes(self) -> List[MacroSize]:
        """
        Compile a list of all macros which have size information, using LEF files.
        This also considers any extra IP libraries.
        :return: List of all macros' size information.
        """

        # Enhance lef_filter to also extract the name of the library.
        def extraction_func(lib: "Library", paths: List[str]) -> List[str]:
            assert len(paths) == 1, "paths_func above returns only one item"
            # For type checker
            lib_name = lib.name  # type: ignore
            if lib_name is None:
                name = ""
            else:
                name = str(lib_name)
            return [json.dumps([paths[0], name])]

        lef_filter_plus = filters.lef_filter._replace(extraction_func=extraction_func)

        lef_names_filenames_serialized = self.process_library_filter(filt=lef_filter_plus,
                                                                     pre_filts=self.default_pre_filters(),
                                                                     output_func=HammerTechnologyUtils.to_plain_item,
                                                                     must_exist=True)

        result = []  # type: List[MacroSize]

        for serialized in lef_names_filenames_serialized:
            lef_filename, name = json.loads(serialized)
            with open(lef_filename, 'r') as f:
                lef_file_contents = str(f.read())
            sizes = LEFUtils.get_sizes(lef_file_contents)
            if len(sizes) == 0:
                continue

            if name == "":
                self.logger.warning(
                    "No name is set for the library containing {lef_filename}".format(lef_filename=lef_filename))

            for s in sizes:
                result.append(MacroSize(
                    library=name,
                    name=s[0],
                    width=s[1],
                    height=s[2]
                ))

        return result

    def get_macro_sizes(self) -> List[MacroSize]:
        """
        Get the list of all macro blocks' sizes for export to other tools.
        :return: List of all macro sizes.
        """
        return self.get_extra_macro_sizes() + self.get_tech_macro_sizes()

    def prepend_dir_path(self, path: str, lib: Optional[Library] = None) -> str:
        """
        Prepend the appropriate path (either from tarballs or installs) to the given library item.
        e.g. if the path argument is "foo/bar" and we have a prefix that defines foo as "/usr/share/foo", then
        this will return "/usr/share/foo/bar".
        :param path: Path to which we should prepend
        :param lib: (optional) Library which produced this path. Used to look for additional prefixes.
        """
        assert len(path) > 0, "path must not be empty"

        # If the path is an absolute path, return it as-is.
        if path[0] == "/":
            return path

        base_path = path.split(os.path.sep)[0]
        rest_of_path = path.split(os.path.sep)[1:]

        if self.config.installs is not None:
            matching_installs = list(filter(lambda install: install.path == base_path, self.config.installs))
        else:
            matching_installs = []

        if self.config.tarballs is not None:
            matching_tarballs = list(filter(lambda tarball: tarball.path == base_path, self.config.tarballs))
        else:
            matching_tarballs = []

        # Some extra typing junk because Library is a dynamically-generated class...
        get_extra_prefixes = lambda l: l.extra_prefixes  # type: Callable[[Any], List[LibraryPrefix]]
        extra_prefixes = get_or_else(optional_map(lib, get_extra_prefixes), [])  # type: List[LibraryPrefix]
        matching_extra_prefixes = list(filter(lambda p: p.prefix == base_path, extra_prefixes))

        matches = len(matching_installs) + len(matching_tarballs) + len(matching_extra_prefixes)
        if matches < 1:
            raise ValueError("Path {0} did not match any tarballs or installs".format(path))
        elif matches > 1:
            raise ValueError("Path {0} matched more than one tarball or install".format(path))
        else:
            if len(matching_installs) == 1:
                install = matching_installs[0]
                if install.base_var == "":
                    base = self.path
                else:
                    base = self.get_setting(install.base_var)
                return os.path.join(*([base] + rest_of_path))
            elif len(matching_tarballs) == 1:
                return os.path.join(self.extracted_tarballs_dir, path)
            else:
                matched = matching_extra_prefixes[0]
                return matched.prepend(os.path.join(*rest_of_path))

    def extract_technology_files(self) -> None:
        """Ensure that the technology files exist either via tarballs or installs."""
        if self.config.installs is not None:
            self.check_installs()
            return
        if self.config.tarballs is not None:
            self.extract_tarballs()
            return
        self.logger.error("Technology specified neither tarballs or installs")

    def check_installs(self) -> bool:
        """Check that the all directories for a pre-installed technology actually exist.

        :return: Return True if the directories is OK, False otherwise."""
        for install in self.config.installs:
            base_var = str(install.base_var)

            if len(base_var) == 0:
                # Blank install_path is okay to reference the current technology directory.
                pass
            else:
                install_path = str(self.get_setting(base_var))
                if not os.path.exists(install_path):
                    self.logger.error("installs {path} does not exist".format(path=install_path))
                    return False
        return True

    def extract_tarballs(self) -> None:
        """Extract tarballs to the given cache_dir, or verify that they've been extracted."""
        for tarball in self.config.tarballs:
            target_path = os.path.join(self.extracted_tarballs_dir, tarball.path)
            tarball_path = os.path.join(self.get_setting(tarball.base_var), tarball.path)
            self.logger.debug("Extracting/verifying tarball %s" % (tarball_path))
            if os.path.isdir(target_path):
                # If the folder already seems to exist, continue
                continue
            else:
                # Else, extract the tarballs.
                os.makedirs(target_path, exist_ok=True)  # Make sure it exists or tar will not be happy.
                subprocess.check_call("tar -xf %s -C %s" % (tarball_path, target_path), shell=True)
                subprocess.check_call("chmod u+rwX -R %s" % (target_path), shell=True)

    def get_extra_libraries(self) -> List[ExtraLibrary]:
        """
        Get the list of extra libraries from the config.
        See vlsi.technology.extra_libraries in defaults.yml.
        :return: List of extra libraries.
        """
        if not self.has_setting("vlsi.technology.extra_libraries"):
            # If the key doesn't exist we can safely say there are no extra libraries.
            return []

        extra_libs = self.get_setting("vlsi.technology.extra_libraries")
        if not isinstance(extra_libs, list):
            raise ValueError("extra_libraries was not a list")
        else:
            return list(map(ExtraLibrary.from_setting, extra_libs))

    def get_available_libraries(self) -> List[Library]:
        """
        Get all available IP libraries. Currently this consists of IP libraries from the technology as well as
        extra IP libraries specified in the config (see get_extra_libraries).
        :return: List of all available IP libraries.
        """
        return list(self.tech_defined_libraries) + list(
            map(lambda el: el.store_into_library(), self.get_extra_libraries()))

    def process_library_filter(self,
                               filt: LibraryFilter,
                               pre_filts: List[Callable[[Library], bool]],
                               output_func: Callable[[str, LibraryFilter], List[str]],
                               must_exist: bool = True,
                               uniquify: bool = True) -> List[str]:
        """
        Process the given library filter and return a list of items from that library filter with any extra
        post-processing.

        - Get a list of lib items
        - Run any extra_post_filter_funcs (if needed)
        - For every lib item in each lib items, run output_func

        :param filt: LibraryFilter to check against all libraries.
        :param pre_filts: List of functions with which to pre-filter the libraries. Each function must return true
                          in order for this library to be used.
        :param output_func: Function which processes the outputs, taking in the filtered lib and the library filter
                            which generated it.
        :param must_exist: Must each library item actually exist? Default: True (yes, they must exist)
        :param uniquify: Must uniqify the list of output files. Default: True
        :return: Resultant items from the filter and post-processed. (e.g. --timing foo.db --timing bar.db)
        """

        # First, filter the list of available libraries with pre_filts and the library itself.
        lib_filters = pre_filts + get_or_else(optional_map(filt.filter_func, lambda x: [x]), [])

        filtered_libs = list(reduce_named(
            sequence=lib_filters,
            initial=self.get_available_libraries(),
            function=lambda libs, func: filter(func, libs)
        ))  # type: List[Library]

        # Next, sort the list of libraries if a sort function exists.
        if filt.sort_func is not None:
            filtered_libs = sorted(filtered_libs, key=filt.sort_func)

        # Next, extract paths and prepend them to get the real paths.
        def get_and_prepend_path(lib: Library) -> Tuple[Library, List[str]]:
            paths = filt.paths_func(lib)
            full_paths = list(map(lambda path: self.prepend_dir_path(path, lib), paths))
            return lib, full_paths

        libs_and_paths = list(map(get_and_prepend_path, filtered_libs))  # type: List[Tuple[Library, List[str]]]

        # Existence checks for paths.
        def check_lib_and_paths(inp: Tuple[Library, List[str]]) -> Tuple[Library, List[str]]:
            lib = inp[0]  # type: Library
            paths = inp[1]  # type: List[str]
            existence_check_func = self.make_check_isfile(filt.description) if filt.is_file else self.make_check_isdir(
                filt.description)
            paths = list(map(existence_check_func, paths))
            return lib, paths

        if must_exist:
            libs_and_paths = list(map(check_lib_and_paths, libs_and_paths))

        # Now call the extraction function to get a final list of strings.

        # If no extraction function was specified, use the identity extraction
        # function.
        def identity_extraction_func(lib: "Library", paths: List[str]) -> List[str]:
            return paths
        extraction_func = get_or_else(filt.extraction_func, identity_extraction_func)

        output_list = reduce_list_str(add_lists, list(map(lambda t: extraction_func(t[0], t[1]), libs_and_paths)), [])  # type: List[str]

        # Quickly check that it is actually a List[str].
        if not isinstance(output_list, List):
            raise TypeError("output_list is not a List[str], but a " + str(type(output_list)))
        for i in output_list:
            if not isinstance(i, str):
                raise TypeError("output_list is a List but not a List[str]")

        # Uniquify results.
        # TODO: think about whether this really belongs here and whether we always need to uniquify.
        # This is here to get stuff working since some CAD tools dislike duplicated arguments (e.g. duplicated stdcell
        # lib, etc).
        if uniquify:
            in_place_unique(output_list)

        # Apply any list-level functions.
        after_post_filter = reduce_named(
            sequence=filt.extra_post_filter_funcs,
            initial=output_list,
            function=lambda libs, func: func(list(libs)),
        )

        # Finally, apply any output functions.
        # e.g. turning foo.db into ["--timing", "foo.db"].
        after_output_functions = list(map(lambda item: output_func(item, filt), after_post_filter))

        # Concatenate lists of List[str] together.
        return reduce_list_str(add_lists, after_output_functions, [])

    def read_libs(self, library_types: Iterable[LibraryFilter], output_func: Callable[[str, LibraryFilter], List[str]],
                  extra_pre_filters: Optional[List[Callable[[Library], bool]]] = None,
                  must_exist: bool = True) -> List[str]:
        """
        Read the given libraries and return a list of strings according to some output format.

        :param library_types: List of libraries to filter, specified as a list of LibraryFilter elements.
        :param output_func: Function which processes the outputs, taking in the filtered lib and the library filter
                            which generated it.
        :param extra_pre_filters: List of additional filter functions to use to filter the list of libraries.
        :param must_exist: Must each library item actually exist? Default: True (yes, they must exist)
        :return: List of filtered libraries processed according output_func.
        """

        pre_filts = self.default_pre_filters()  # type: List[Callable[[Library], bool]]
        if extra_pre_filters is not None:
            assert isinstance(extra_pre_filters, List)
            pre_filts += extra_pre_filters

        return reduce_list_str(
            add_lists,
            map(
                lambda lib: self.process_library_filter(pre_filts=pre_filts, filt=lib, output_func=output_func, must_exist=must_exist),
                library_types
            )
        )

    def default_pre_filters(self) -> List[Callable[[Library], bool]]:
        """
        Get the list of default pre-filters to pre-filter out IP libraries
        before processing a LibraryFilter.
        """
        return [self.filter_for_supplies]

    def filter_for_supplies(self, lib: Library) -> bool:
        """Function to help filter a list of libraries to find libraries which have matching supplies.
        Will also use libraries with no supplies annotation.

        :param lib: Library to check
        :return: True if the supplies of this library match the inputs for this run, False otherwise.
        """
        if lib.supplies is None:
            # TODO: add some sort of wildcard value for supplies for libraries which _actually_ should
            # always be used.
            self.logger.warning("Lib %s has no supplies annotation! Using anyway." % (lib.serialize()))
            return True
        # If we are using MMMC assume all libraries will be used.
        # TODO: Read the corners and filter out libraries that don't match any of them.
        # Requires a refactor because MMMCCorner parsing is only in HammerTool now.
        # See issue #275.
        if self.get_setting("vlsi.inputs.mmmc_corners"):
            return True
        return self.get_setting("vlsi.inputs.supplies.VDD") == lib.supplies.VDD and self.get_setting("vlsi.inputs.supplies.GND") == lib.supplies.GND

    @staticmethod
    def make_check_isdir(description: str = "Path") -> Callable[[str], str]:
        """
        Utility function to generate functions which check whether a path exists.
        """
        def check_isdir(path: str) -> str:
            if not os.path.isdir(path):
                raise ValueError("%s %s is not a directory or does not exist" % (description, path))
            else:
                return path
        return check_isdir

    @staticmethod
    def make_check_isfile(description: str = "File") -> Callable[[str], str]:
        """
        Utility function to generate functions which check whether a path exists.
        """
        def check_isfile(path: str) -> str:
            if not os.path.isfile(path):
                raise ValueError("%s %s is not a file or does not exist" % (description, path))
            else:
                return path

        return check_isfile

    def get_stackup_by_name(self, name: str) -> Stackup:
        """
        Return the stackup details for the given key.
        """
        if self.config.stackups is not None:
            for item in list(self.config.stackups):
                if item["name"] == name:
                    return Stackup.from_setting(self.get_grid_unit(), item)
            raise ValueError("Stackup named %s is not defined in tech JSON" % name)
        else:
            raise ValueError("Tech JSON does not specify any stackups")

    def get_grid_unit(self) -> Decimal:
        """
        Return the manufacturing grid unit.
        """
        if self.config.grid_unit is not None:
            return Decimal(self.config.grid_unit)
        else:
            raise ValueError("Tech JSON does not specify a manufacturing grid unit")

    def get_site_by_name(self, name: str) -> Site:
        """
        Return the site for the given key.
        """
        if self.config.sites is not None:
            for item in list(self.config.sites):
                if item["name"] == name:
                    return Site.from_setting(self.get_grid_unit(), item)
            raise ValueError("Site named %s is not defined in tech JSON" % name)
        else:
            raise ValueError("Tech JSON does not specify any sites")

    def get_placement_site(self) -> Site:
        """
        Return the default placement site defined by the hammer setting "vlsi.technology.placement_site"
        """
        return self.get_site_by_name(self.get_setting("vlsi.technology.placement_site"))


class HammerTechnologyUtils:
    """
    Utility/helper functions for HammerTechnology.
    """

    @staticmethod
    def to_command_line_args(lib_item: str, filt: LibraryFilter) -> List[str]:
        """
        Generate command-line args in the form --<filt.tag> <lib_item>.
        """
        return ["--" + filt.tag, lib_item]

    @staticmethod
    def to_plain_item(lib_item: str, filt: LibraryFilter) -> List[str]:
        """
        Generate plain outputs in the form of <lib_item1> <lib_item2> ...
        """
        return [lib_item]

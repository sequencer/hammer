#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#  hammer_vlsi_impl.py
#  hammer-vlsi implementation file. Users should import hammer_vlsi instead.
#
#  See LICENSE for licence details.

from abc import abstractmethod
from enum import Enum
from functools import reduce
import importlib
from numbers import Number
import os
import sys
import json
from typing import Callable, Iterable, List, NamedTuple, Optional, Dict, Any, Union
from decimal import Decimal

import hammer_config
from hammer_utils import reverse_dict, deepdict, optional_map, get_or_else, add_dicts, coerce_to_grid
from hammer_tech import Library, ExtraLibrary

from .constraints import *
from .units import VoltageValue


class HierarchicalMode(Enum):
    Flat = 1
    Leaf = 2
    Hierarchical = 3
    Top = 4

    @classmethod
    def __mapping(cls) -> Dict[str, "HierarchicalMode"]:
        return {
            "flat": HierarchicalMode.Flat,
            "leaf": HierarchicalMode.Leaf,
            "hierarchical": HierarchicalMode.Hierarchical,
            "top": HierarchicalMode.Top
        }

    @staticmethod
    def from_str(x: str) -> "HierarchicalMode":
        try:
            return HierarchicalMode.__mapping()[x]
        except KeyError:
            raise ValueError("Invalid string for HierarchicalMode: " + str(x))

    def __str__(self) -> str:
        return reverse_dict(HierarchicalMode.__mapping())[self]

    def is_nonleaf_hierarchical(self) -> bool:
        """
        Helper function that returns True if this mode is a non-leaf hierarchical mode (i.e. any block with
        hierarchical sub-blocks).
        """
        return self == HierarchicalMode.Hierarchical or self == HierarchicalMode.Top

class HammerToolPauseException(Exception):
    """
    Internal hammer-vlsi exception raised to indicate that a step has stopped execution of the tool.
    This is not necessarily an error condition.
    """
    pass


import hammer_tech


class HammerVLSISettings:
    """
    Static class which holds global hammer-vlsi settings.
    """
    hammer_vlsi_path = ""  # type: str

    @staticmethod
    def get_config() -> dict:
        """Export settings as a config dictionary."""
        return {
            "vlsi.builtins.hammer_vlsi_path": HammerVLSISettings.hammer_vlsi_path
        }

    @classmethod
    def set_hammer_vlsi_path_from_environment(cls) -> bool:
        """
        Try to set hammer_vlsi_path from the environment variable HAMMER_VLSI.

        :return: True if successfully set, False otherwise
        """
        if "HAMMER_VLSI" not in os.environ:
            return False
        else:
            cls.hammer_vlsi_path = os.environ["HAMMER_VLSI"]
            return True

    @classmethod
    def load_builtins_and_core(cls, database: hammer_config.HammerDatabase) -> None:
        """
        Helper function that loads builtins and core into a HammerDatabase.
        """

        # Load in builtins.
        builtins_path = os.path.join(cls.hammer_vlsi_path, "builtins.yml")
        if not os.path.exists(builtins_path):
            raise FileNotFoundError(
                "hammer-vlsi builtin settings not found. Did you call HammerVLSISettings.set_hammer_vlsi_path_from_environment()?")

        database.update_builtins([
            hammer_config.load_config_from_file(builtins_path, strict=True),
            HammerVLSISettings.get_config()
        ])

        # Read in core defaults.
        database.update_core(hammer_config.load_config_from_defaults(cls.hammer_vlsi_path, strict=True))


from .hammer_tool import HammerTool, HammerToolStep

class DummyHammerTool(HammerTool):
    """
    This is a dummy implementation of HammerTool that does nothing.
    It has no config, and no particular sense of versioning.
    It is present for nop tools and as a testing aid.
    """

    def tool_config_prefix(self) -> str:
        return ""

    def version_number(self, version: str) -> int:
        return 1

    @property
    def steps(self) -> List[HammerToolStep]:
        return []

class HammerSRAMGeneratorTool(HammerTool):
    ### Generated interface HammerSRAMGeneratorTool ###
    ### DO NOT MODIFY THIS CODE, EDIT generate_properties.py INSTEAD ###
    ### Inputs ###

    @property
    def input_parameters(self) -> List[SRAMParameters]:
        """
        Get the input sram parameters to be generated.

        :return: The input sram parameters to be generated.
        """
        try:
            return self.attr_getter("_input_parameters", None)
        except AttributeError:
            raise ValueError("Nothing set for the input sram parameters to be generated yet")

    @input_parameters.setter
    def input_parameters(self, value: List[SRAMParameters]) -> None:
        """Set the input sram parameters to be generated."""
        if not (isinstance(value, List)):
            raise TypeError("input_parameters must be a List[SRAMParameters]")
        self.attr_setter("_input_parameters", value)


    ### Outputs ###

    @property
    def output_libraries(self) -> List[ExtraLibrary]:
        """
        Get the list of the hammer tech libraries corresponding to generated srams.

        :return: The list of the hammer tech libraries corresponding to generated srams.
        """
        try:
            return self.attr_getter("_output_libraries", None)
        except AttributeError:
            raise ValueError("Nothing set for the list of the hammer tech libraries corresponding to generated srams yet")

    @output_libraries.setter
    def output_libraries(self, value: List[ExtraLibrary]) -> None:
        """Set the list of the hammer tech libraries corresponding to generated srams."""
        if not (isinstance(value, List)):
            raise TypeError("output_libraries must be a List[ExtraLibrary]")
        self.attr_setter("_output_libraries", value)

    ### END Generated interface HammerSRAMGeneratorTool ###

    @property
    def steps(self) -> List[HammerToolStep]:
        steps = [
            self.generate_all_srams_and_corners
            ]
        return self.make_steps_from_methods(steps)

    def fill_outputs(self) -> bool:
        return True #we fill in output_libraries in generate_all_srams_and_corners

    def export_config_outputs(self) -> Dict[str, Any]:
        outputs = deepdict(super().export_config_outputs())
        simple_ex = []
        for ex in self.output_libraries: # type: ExtraLibrary
            simple_lib = json.loads(ex.library.serialize())
            if(ex.prefix == None):
                new_ex = {"library": simple_lib}
            else:
                new_ex = {"prefix": ex.prefix, "library": simple_lib}
            simple_ex.append(new_ex)
        outputs["vlsi.technology.extra_libraries"] = simple_ex
        outputs["vlsi.technology.extra_libraries_meta"] = "append"
        return outputs

    #TODO: Is this the right way for these two generate_all methods to work
    # in techX16 you can generate only ever generate a single SRAM per run but can
    # generate multiple corners at once
    def generate_all_srams_and_corners(self) -> bool:
        srams = reduce(list.__add__, list(map(lambda c: self.generate_all_srams(c), self.get_mmmc_corners()))) # type: List[ExtraLibrary]
        self.output_libraries = srams
        return True

    def generate_all_srams(self, corner: MMMCCorner) -> List[ExtraLibrary]:
        srams = list(map(lambda p: self.generate_sram(p, corner), self.input_parameters)) # type: List[ExtraLibrary]
        return srams

    # Run compiler for a single sram and corner
    @abstractmethod
    def generate_sram(self, params: SRAMParameters, corner: MMMCCorner) -> ExtraLibrary:
        pass

class HammerSynthesisTool(HammerTool):
    @abstractmethod
    def fill_outputs(self) -> bool:
        pass

    def export_config_outputs(self) -> Dict[str, Any]:
        outputs = deepdict(super().export_config_outputs())
        outputs["synthesis.outputs.output_files"] = self.output_files
        outputs["synthesis.inputs.input_files"] = self.input_files
        outputs["synthesis.inputs.top_module"] = self.top_module
        return outputs

    ### Generated interface HammerSynthesisTool ###
    ### DO NOT MODIFY THIS CODE, EDIT generate_properties.py INSTEAD ###
    ### Inputs ###

    @property
    def input_files(self) -> List[str]:
        """
        Get the input collection of source RTL files (e.g. *.v).

        :return: The input collection of source RTL files (e.g. *.v).
        """
        try:
            return self.attr_getter("_input_files", None)
        except AttributeError:
            raise ValueError("Nothing set for the input collection of source RTL files (e.g. *.v) yet")

    @input_files.setter
    def input_files(self, value: List[str]) -> None:
        """Set the input collection of source RTL files (e.g. *.v)."""
        if not (isinstance(value, List)):
            raise TypeError("input_files must be a List[str]")
        self.attr_setter("_input_files", value)


    ### Outputs ###

    @property
    def output_files(self) -> List[str]:
        """
        Get the output collection of mapped (post-synthesis) RTL files.

        :return: The output collection of mapped (post-synthesis) RTL files.
        """
        try:
            return self.attr_getter("_output_files", None)
        except AttributeError:
            raise ValueError("Nothing set for the output collection of mapped (post-synthesis) RTL files yet")

    @output_files.setter
    def output_files(self, value: List[str]) -> None:
        """Set the output collection of mapped (post-synthesis) RTL files."""
        if not (isinstance(value, List)):
            raise TypeError("output_files must be a List[str]")
        self.attr_setter("_output_files", value)


    @property
    def output_sdc(self) -> str:
        """
        Get the (optional) output post-synthesis SDC constraints file.

        :return: The (optional) output post-synthesis SDC constraints file.
        """
        try:
            return self.attr_getter("_output_sdc", None)
        except AttributeError:
            raise ValueError("Nothing set for the (optional) output post-synthesis SDC constraints file yet")

    @output_sdc.setter
    def output_sdc(self, value: str) -> None:
        """Set the (optional) output post-synthesis SDC constraints file."""
        if not (isinstance(value, str)):
            raise TypeError("output_sdc must be a str")
        self.attr_setter("_output_sdc", value)

    ### END Generated interface HammerSynthesisTool ###
    ### Generated interface HammerSynthesisTool ###


class HammerPlaceAndRouteTool(HammerTool):
    @abstractmethod
    def fill_outputs(self) -> bool:
        pass

    def export_config_outputs(self) -> Dict[str, Any]:
        outputs = deepdict(super().export_config_outputs())
        outputs["par.outputs.output_ilms"] = list(map(lambda s: s.to_setting(), self.output_ilms))
        outputs["par.outputs.output_ilms_meta"] = "append"
        outputs["par.outputs.output_gds"] = str(self.output_gds)
        outputs["par.outputs.output_netlist"] = str(self.output_netlist)
        outputs["par.outputs.hcells_list"] = list(self.hcells_list)
        return outputs

    ### Generated interface HammerPlaceAndRouteTool ###
    ### DO NOT MODIFY THIS CODE, EDIT generate_properties.py INSTEAD ###
    ### Inputs ###

    @property
    def input_files(self) -> List[str]:
        """
        Get the input post-synthesis netlist files.

        :return: The input post-synthesis netlist files.
        """
        try:
            return self.attr_getter("_input_files", None)
        except AttributeError:
            raise ValueError("Nothing set for the input post-synthesis netlist files yet")

    @input_files.setter
    def input_files(self, value: List[str]) -> None:
        """Set the input post-synthesis netlist files."""
        if not (isinstance(value, List)):
            raise TypeError("input_files must be a List[str]")
        self.attr_setter("_input_files", value)


    @property
    def post_synth_sdc(self) -> Optional[str]:
        """
        Get the (optional) input post-synthesis SDC constraint file.

        :return: The (optional) input post-synthesis SDC constraint file.
        """
        try:
            return self.attr_getter("_post_synth_sdc", None)
        except AttributeError:
            return None

    @post_synth_sdc.setter
    def post_synth_sdc(self, value: Optional[str]) -> None:
        """Set the (optional) input post-synthesis SDC constraint file."""
        if not (isinstance(value, str) or (value is None)):
            raise TypeError("post_synth_sdc must be a Optional[str]")
        self.attr_setter("_post_synth_sdc", value)


    ### Outputs ###

    @property
    def output_ilms(self) -> List[ILMStruct]:
        """
        Get the (optional) output ILM information for hierarchical mode.

        :return: The (optional) output ILM information for hierarchical mode.
        """
        try:
            return self.attr_getter("_output_ilms", None)
        except AttributeError:
            raise ValueError("Nothing set for the (optional) output ILM information for hierarchical mode yet")

    @output_ilms.setter
    def output_ilms(self, value: List[ILMStruct]) -> None:
        """Set the (optional) output ILM information for hierarchical mode."""
        if not (isinstance(value, List)):
            raise TypeError("output_ilms must be a List[ILMStruct]")
        self.attr_setter("_output_ilms", value)


    @property
    def output_gds(self) -> str:
        """
        Get the path to the output GDS file.

        :return: The path to the output GDS file.
        """
        try:
            return self.attr_getter("_output_gds", None)
        except AttributeError:
            raise ValueError("Nothing set for the path to the output GDS file yet")

    @output_gds.setter
    def output_gds(self, value: str) -> None:
        """Set the path to the output GDS file."""
        if not (isinstance(value, str)):
            raise TypeError("output_gds must be a str")
        self.attr_setter("_output_gds", value)


    @property
    def output_netlist(self) -> str:
        """
        Get the path to the output netlist file.

        :return: The path to the output netlist file.
        """
        try:
            return self.attr_getter("_output_netlist", None)
        except AttributeError:
            raise ValueError("Nothing set for the path to the output netlist file yet")

    @output_netlist.setter
    def output_netlist(self, value: str) -> None:
        """Set the path to the output netlist file."""
        if not (isinstance(value, str)):
            raise TypeError("output_netlist must be a str")
        self.attr_setter("_output_netlist", value)


    @property
    def hcells_list(self) -> List[str]:
        """
        Get the list of cells to explicitly map hierarchically in LVS.

        :return: The list of cells to explicitly map hierarchically in LVS.
        """
        try:
            return self.attr_getter("_hcells_list", None)
        except AttributeError:
            raise ValueError("Nothing set for the list of cells to explicitly map hierarchically in LVS yet")

    @hcells_list.setter
    def hcells_list(self, value: List[str]) -> None:
        """Set the list of cells to explicitly map hierarchically in LVS."""
        if not (isinstance(value, List)):
            raise TypeError("hcells_list must be a List[str]")
        self.attr_setter("_hcells_list", value)

    ### END Generated interface HammerPlaceAndRouteTool ###

    def create_power_straps_tcl(self) -> List[str]:
        """
        Create power straps TCL commands depending on the mode.
        """
        output = []  # type: List[str]

        power_straps_mode = str(self.get_setting("par.power_straps_mode"))
        if power_straps_mode == "manual":
            power_straps_script_contents = str(self.get_setting("par.power_straps_script_contents"))
            # TODO(edwardw): proper source locators/SourceInfo
            output.append("# Power straps script manually specified from HAMMER")
            output.extend(power_straps_script_contents.split("\n"))
        elif power_straps_mode == "generate":
            output.extend(self.generate_power_straps_tcl())
        else:
            if power_straps_mode != "empty":
                self.logger.error(
                    "Invalid power_straps_mode {mode}. Using blank power straps script.".format(mode=power_straps_mode))
            # Write blank power straps
            output.append("# Blank power straps script specified from HAMMER")
        return output

    def generate_power_straps_tcl(self) -> List[str]:
        """
        Generate a TCL script to create power straps from the config/IR.
        :return: Power straps TCL script.
        """
        method = self.get_setting("par.generate_power_straps_method")
        if method == "by_tracks":
            # By default put straps everywhere
            bbox = None # type: Optional[List[Decimal]]
            namespace = "par.generate_power_straps_options.by_tracks"
            layers = self.get_setting("{}.strap_layers".format(namespace))
            pin_layers = self.get_setting("{}.pin_layers".format(namespace))
            ground_net_names = list(map(lambda x: x.name, self.get_independent_ground_nets()))  # type: List[str]
            power_net_names = list(map(lambda x: x.name, self.get_independent_power_nets()))  # type: List[str]
            def get_weight(s: Supply) -> int:
                # Check that it's not None
                assert isinstance(s.weight, int)
                return s.weight
            weights = list(map(get_weight, self.get_independent_power_nets()))  # type: List[int]
            assert len(ground_net_names) == 1, "FIXME, I am assuming there's only 1 ground net"
            return self.specify_all_power_straps_by_tracks(layers, ground_net_names[0], power_net_names, weights, bbox, pin_layers)
        else:
            raise NotImplementedError("Power strap generation method %s is not implemented" % method)

    def specify_power_straps_by_tracks(self, layer_name: str, bottom_via_layer: str, blockage_spacing: Decimal, track_pitch: int, track_width: int, track_spacing: int, track_start: int, track_offset: Decimal, bbox: Optional[List[Decimal]], nets: List[str], add_pins: bool, layer_is_all_power: bool) -> List[str]:
        """
        Generate a list of TCL commands that will create power straps on a given layer by specifying the desired track consumption.
        This method assumes that power straps are built bottom-up, starting with standard cell rails.

        :param layer_name: The layer name of the metal on which to create straps.
        :param bottom_via_layer_name: The layer name of the lowest metal layer down to which to drop vias.
        :param blockage_spacing: The minimum spacing between the end of a strap and the beginning of a macro or blockage.
        :param track_pitch: The integer pitch between groups of power straps (i.e. from left edge of strap A to the next left edge of strap A) in units of the routing pitch.
        :param track_width: The desired number of routing tracks to consume by a single power strap.
        :param track_spacing: The desired number of USABLE routing tracks between power straps. It is recommended to leave this at 0 except to fix DRC issues.
        :param track_start: The index of the first track to start using for power straps relative to the bounding box.
        :param bbox: The optional (2N)-point bounding box of the area to generate straps. By default the entire core area is used.
        :param nets: A list of power nets to create (e.g. ["VDD", "VSS"], ["VDDA", "VSS", "VDDB"], ... etc.).
        :param add_pins: True if pins are desired on this layer; False otherwise.
        :param layer_is_all_power: True if there will be no signal wires on this layer.
        :return: A list of TCL commands that will generate power straps.
        """
        # Note: even track_widths will be snapped to a half-track
        layer = self.get_stackup().get_metal(layer_name)
        pitch = track_pitch * layer.pitch
        width = Decimal(0)
        spacing = Decimal(0)
        strap_offset = Decimal(0)
        if track_spacing == 0:
            # An all-power (100% utilization) layer results in us wanting to do a uniform strap pattern, so we can just calculate the
            # maximum width and minimum spacing from the desired pitch, instead of using TWWT.
            if layer_is_all_power:
                one_strap_pitch = track_width * layer.pitch
                spacing, width = layer.min_spacing_and_max_width_from_pitch(one_strap_pitch)
                strap_start = spacing / 2 + layer.offset
            else:
                width, spacing, strap_start = layer.get_width_spacing_start_twwt(track_width, force_even=True)
        else:
            width, spacing, strap_start = layer.get_width_spacing_start_twt(track_width)
            spacing = 2*spacing + (track_spacing - 1) * layer.pitch + layer.min_width
        offset = track_offset + track_start * layer.pitch + strap_start
        assert width > Decimal(0), "Width must be greater than zero. You probably have a malformed tech plugin on layer {}.".format(layer_name)
        assert spacing > Decimal(0), "Spacing must be greater than zero. You probably have a malformed tech plugin on layer {}.".format(layer_name)
        return self.specify_power_straps(layer_name, bottom_via_layer, blockage_spacing, pitch, width, spacing, offset, bbox, nets, add_pins)

    def specify_all_power_straps_by_tracks(self, layer_names: List[str], ground_net: str, power_nets: List[str], power_weights: List[int], bbox: Optional[List[Decimal]], pin_layers: List[str]) -> List[str]:
        """
        Generate a list of TCL commands that will create power straps on a given set of layers by specifying the desired per-track track consumption and utilization.
        This will build standard cell power strap rails first. Layer-specific parameters are read from the hammer config:
            - par.generate_power_straps_options.by_tracks.blockage_spacing
            - par.generate_power_straps_options.by_tracks.track_width
            - par.generate_power_straps_options.by_tracks.track_spacing
            - par.generate_power_straps_options.by_tracks.power_utilization
        These settings are all overridable by appending an underscore followed by the metal name (e.g. power_utilization_M3).

        :param layer_names: The list of metal layer names on which to create straps.
        :param ground_net: The name of the ground net in this design. Only 1 ground net is supported.
        :param power_nets: A list of power nets to create (not ground).
        :param power_weights: Specifies the power strap placement pattern for multiple-domain designs (e.g. ["VDDA", "VDDB"] with [2, 1] will produce 2 VDDA straps for ever 1 VDDB strap).
        :param bbox: The optional (2N)-point bounding box of the area to generate straps. By default the entire core area is used.
        :param pin_layers: A list of layers on which to place pins
        :return: A list of TCL commands that will generate power straps.
        """
        assert len(power_nets) == len(power_weights)

        # Do some sanity checking
        for l in pin_layers:
            assert l in layer_names, "Pin layer {} must be in power strap layers".format(l)

        rail_layer_name = self.get_setting("technology.core.std_cell_rail_layer")
        rail_layer = self.get_stackup().get_metal(rail_layer_name)
        blockage_spacing = coerce_to_grid(float(self._get_by_tracks_metal_setting("blockage_spacing", rail_layer_name)), rail_layer.grid_unit)
        # TODO does the CPF help this, or do we need to be more explicit about the bbox for each domain
        output = self.specify_std_cell_power_straps(blockage_spacing, bbox, [ground_net] + power_nets)
        # The layer to via down to
        bottom_via_layer = rail_layer_name
        # The last layer we used
        last = rail_layer
        for layer_name in layer_names:
            layer = self.get_stackup().get_metal(layer_name)
            assert layer.index > last.index, "Must build power straps bottom-up"
            if last.direction == layer.direction:
                raise ValueError("Layers {a} and {b} run in the same direction, but have no power straps between them.".format(a=last.name, b=layer.name))

            blockage_spacing = coerce_to_grid(float(self._get_by_tracks_metal_setting("blockage_spacing", layer_name)), layer.grid_unit)
            track_width = int(self._get_by_tracks_metal_setting("track_width", layer_name))
            track_spacing = int(self._get_by_tracks_metal_setting("track_spacing", layer_name))
            track_start = int(self._get_by_tracks_metal_setting("track_start", layer_name))
            track_pitch = self._get_by_tracks_track_pitch(layer_name)
            offset = layer.offset # TODO this is relaxable if we can auto-recalculate this based on hierarchical setting

            add_pins = layer_name in pin_layers
            # For multiple domains, we'll stripe them like this:
            # 2:1 :   A A B A A B ...
            # 3:1 :   A A A B A A A B ...
            # 3:2 :   A A A B B A A A B B ...
            # 2:2:1 : A A B B C A A B B C ...
            sum_weights = sum(power_weights)
            # If the power + ground tracks are equal to the pitch, we have no signals
            layer_is_all_power = (2 * track_width) == track_pitch
            for i in range(sum_weights):
                nets = [ground_net, power_nets[i]]
                group_offset = offset + track_pitch * i * layer.pitch
                group_pitch = sum_weights * track_pitch
                output.extend(self.specify_power_straps_by_tracks(layer_name, last.name, blockage_spacing, group_pitch, track_width, track_spacing, track_start, group_offset, bbox, nets, add_pins, layer_is_all_power))
            last = layer
        return output

    _power_straps_last_index = -1

    def _power_straps_check_index(self, layer_name: str) -> None:
        next_index = self.get_stackup().get_metal(layer_name).index
        assert next_index >= self._power_straps_last_index, "Must construct power straps from bottom to top"
        self._power_straps_last_index = next_index

    def _get_by_tracks_metal_setting(self, key: str, layer_name: str) -> Any:
        """
        Return the metal setting used by the by_tracks power strap generation method.
        This will return the value from the provided key in the par.generate_power_straps.by_tracks namespace,
        which can be overridden for a specific metal layer by appending _<layer name>.

        :param key: The base key name (e.g. track_spacing). Do not include the namespace or metal override.
        :return: The value associated with the key, after applying any metal overrides
        """
        default = "par.generate_power_straps_options.by_tracks." + key
        override = default + "_" + layer_name
        try:
            return self.get_setting(override)
        except KeyError:
            try:
                return self.get_setting(default)
            except KeyError:
                raise ValueError("No value set for key {}".format(default))

    def _get_by_tracks_track_pitch(self, layer_name: str) -> int:
        """
        Returns the track pitch used by the by_tracks power rail generation method

        :param layer_name: The string name of the metal layer
        :return: The power strap group pitch in tracks
        """
        track_width = int(self._get_by_tracks_metal_setting("track_width", layer_name))
        track_spacing = int(self._get_by_tracks_metal_setting("track_spacing", layer_name))
        power_utilization = float(self._get_by_tracks_metal_setting("power_utilization", layer_name))

        assert power_utilization > 0.0
        assert power_utilization <= 1.0

        # Calculate how many tracks we consume
        # This strategy uses pairs of power and ground
        consumed_tracks = 2 * track_width + track_spacing
        return round(consumed_tracks / power_utilization)

    @abstractmethod
    def specify_power_straps(self, layer_name: str, bottom_via_layer_name: str, blockage_spacing: Decimal, pitch: Decimal, width: Decimal, spacing: Decimal, offset: Decimal, bbox: Optional[List[Decimal]], nets: List[str], add_pins: bool) -> List[str]:
        """
        Generate a list of TCL commands that will create power straps on a given layer.
        This is a low-level, cad-tool-specific API. It is designed to be called by higher-level methods, so calling this directly is not recommended.
        This method assumes that power straps are built bottom-up, starting with standard cell rails.

        :param layer_name: The layer name of the metal on which to create straps.
        :param bottom_via_layer_name: The layer name of the lowest metal layer down to which to drop vias.
        :param blockage_spacing: The minimum spacing between the end of a strap and the beginning of a macro or blockage.
        :param pitch: The pitch between groups of power straps (i.e. from left edge of strap A to the next left edge of strap A).
        :param width: The width of each strap in a group.
        :param spacing: The spacing between straps in a group.
        :param offset: The offset to start the first group.
        :param bbox: The optional (2N)-point bounding box of the area to generate straps. By default the entire core area is used.
        :param nets: A list of power nets to create (e.g. ["VDD", "VSS"], ["VDDA", "VSS", "VDDB"],  ... etc.).
        :param add_pins: True if pins are desired on this layer; False otherwise.
        :return: A list of TCL commands that will generate power straps.
        """
        # This should get overriden but be sure to use this check in your implementations
        self._power_straps_check_index(layer_name)
        return []

    @abstractmethod
    def specify_std_cell_power_straps(self, blockage_spacing: Decimal, bbox: Optional[List[Decimal]], nets: List[str]) -> List[str]:
        """
        Generate a list of TCL commands that build the low-level standard cell power strap rails.
        This is a low-level, cad-tool-specific API. It is designed to be called by higher-level methods, so calling this directly is not recommended.
        This will create power straps based on technology.core.tap_cell_rail_reference.
        The layer is set by technology.core.std_cell_rail_layer, which should be the highest metal layer in the std cell rails.
        This method should be called before any calls to specify_power_straps.

        :param blockage_spacing: The spacing to leave between the end of a stripe and a macro or routing blockage.
        :param bbox: The optional (2N)-point bounding box of the area to generate straps. By default the entire core area is used.
        :param nets: A list of power net names (e.g. ["VDD", "VSS"]).
        :return: A list of TCL commands that will generate power straps on rails.
        """
        # This should get overriden but be sure to use this check in your implementations
        layer_name = self.get_setting("technology.core.std_cell_rail_layer")
        self._power_straps_check_index(layer_name)
        return []


class HammerSignoffTool(HammerTool):
    @abstractmethod
    def fill_outputs(self) -> bool:
        pass

    ### Inputs ###

    ### Outputs ###
    @abstractmethod
    def signoff_results(self) -> int:
        """
        Return the number of issues raised by the signoff tool (0 = all checks pass).
        Individual tools extending HammerSignoffTool should implement their own *_results methods that provide tool-specific information,
        and then pass a meaningful count of issues to their implementation of this method.

        :return: The number of signoff issues raised by the tool
        """
        pass

class HammerDRCTool(HammerSignoffTool):

    @abstractmethod
    def fill_outputs(self) -> bool:
        pass

    @abstractmethod
    def globally_waived_drc_rules(self) -> List[str]:
        # TODO(johnwright) how to waive specific instances of DRC rules, rather than blanket waivers
        # TODO(johnwright) should this go in the YAML file?
        """
        Get the list of waived DRC rule names.

        :return: The list of waived DRC rule names.
        """
        pass

    def get_additional_drc_text(self) -> str:
        """ Get the additional custom DRC command text to add after the boilerplate commands at the top of the DRC run file. """

        # Mode can be auto, manual, append, or prepend
        add_drc_text_mode = str(self.get_setting("drc.inputs.additional_drc_text_mode"))

        # manul_add_drc_text will only be used in manual, append, and prepend modes
        manual_add_drc_text = str(self.get_setting("drc.inputs.additional_drc_text"))

        # tech_add_drc_text will only be used in auto, append, and prepend modes
        tech_add_drc_text = get_or_else(self.technology.additional_drc_text, "") # type: str

        # Default to auto (use tech_add_drc_text)
        add_drc_text = tech_add_drc_text

        if add_drc_text_mode == "auto":
            pass
        elif add_drc_text_mode == "manual":
            add_drc_text = manual_add_drc_text
        elif add_drc_text_mode == "append":
            add_drc_text = tech_add_drc_text + manual_add_drc_text
        elif add_drc_text_mode == "prepend":
            add_drc_text = manual_add_drc_text + tech_add_drc_text
        else:
            self.logger.error(
                "Invalid additional_drc_text_mode {mode}. Using auto.".format(mode=add_drc_text_mode))

        return add_drc_text

    @abstractmethod
    def drc_results_pre_waived(self) -> Dict[str, int]:
        """ Return a Dict mapping the DRC check name to an error count (pre-waivers). """
        pass

    def signoff_results(self) -> int:
        """ Return the count of unwaived DRC errors. """
        return sum(self.drc_results().values())

    def drc_results(self) -> Dict[str, int]:
        """ Return a Dict mapping the DRC check name to an error count (with waivers). """
        res = self.drc_results_pre_waived()
        return {k: 0 if k in self.globally_waived_drc_rules() else int(res[k]) for k in res}

    ### Generated interface HammerDRCTool ###
    ### DO NOT MODIFY THIS CODE, EDIT generate_properties.py INSTEAD ###
    ### Inputs ###

    @property
    def layout_file(self) -> str:
        """
        Get the path to the input layout file (e.g. a *.gds).

        :return: The path to the input layout file (e.g. a *.gds).
        """
        try:
            return self.attr_getter("_layout_file", None)
        except AttributeError:
            raise ValueError("Nothing set for the path to the input layout file (e.g. a *.gds) yet")

    @layout_file.setter
    def layout_file(self, value: str) -> None:
        """Set the path to the input layout file (e.g. a *.gds)."""
        if not (isinstance(value, str)):
            raise TypeError("layout_file must be a str")
        self.attr_setter("_layout_file", value)


    ### Outputs ###
    ### END Generated interface HammerDRCTool ###


class HammerLVSTool(HammerSignoffTool):
    @abstractmethod
    def fill_outputs(self) -> bool:
        pass

    @abstractmethod
    def globally_waived_erc_rules(self) -> List[str]:
        # TODO(johnwright) how to waive specific instances of ERC rules, rather than blanket waivers
        # TODO(johnwright) should this go in the YAML file?
        """
        Get the list of waived ERC rule names.

        :return: The list of waived ERC rule names.
        """
        pass

    @abstractmethod
    def erc_results_pre_waived(self) -> Dict[str, int]:
        """ Return a Dict mapping the ERC check name to an error count (pre-waivers). """
        pass

    def signoff_results(self) -> int:
        """ Return the count of unwaived ERC errors and LVS errors. """
        return sum(self.erc_results().values()) + len(self.lvs_results())

    def erc_results(self) -> Dict[str, int]:
        """ Return a Dict mapping the ERC check name to an error count (with waivers). """
        res = self.erc_results_pre_waived()
        return {k: 0 if k in self.globally_waived_erc_rules() else int(res[k]) for k in res}

    @abstractmethod
    def lvs_results(self) -> List[str]:
        """ Return the LVS issue descriptions for each issue. An empty list means LVS passes. """
        pass

    def get_additional_lvs_text(self) -> str:
        """ Get the additional custom LVS command text to add after the boilerplate commands at the top of the LVS run file. """

        # Mode can be auto, manual, append, or prepend
        add_lvs_text_mode = str(self.get_setting("lvs.inputs.additional_lvs_text_mode"))

        # manul_add_lvs_text will only be used in manual, append, and prepend modes
        manual_add_lvs_text = str(self.get_setting("lvs.inputs.additional_lvs_text"))

        # tech_add_lvs_text will only be used in auto, append, and prepend modes
        tech_add_lvs_text = get_or_else(self.technology.additional_lvs_text, "") # type: str

        # Default to auto (use tech_add_lvs_text)
        add_lvs_text = tech_add_lvs_text

        if add_lvs_text_mode == "auto":
            pass
        elif add_lvs_text_mode == "manual":
            add_lvs_text = manual_add_lvs_text
        elif add_lvs_text_mode == "append":
            add_lvs_text = tech_add_lvs_text + manual_add_lvs_text
        elif add_lvs_text_mode == "prepend":
            add_lvs_text = manual_add_lvs_text + tech_add_lvs_text
        else:
            self.logger.error(
                "Invalid additional_lvs_text_mode {mode}. Using auto.".format(mode=add_lvs_text_mode))

        return add_lvs_text

    ### Generated interface HammerLVSTool ###
    ### DO NOT MODIFY THIS CODE, EDIT generate_properties.py INSTEAD ###
    ### Inputs ###

    @property
    def layout_file(self) -> str:
        """
        Get the path to the input layout file (e.g. a *.gds).

        :return: The path to the input layout file (e.g. a *.gds).
        """
        try:
            return self.attr_getter("_layout_file", None)
        except AttributeError:
            raise ValueError("Nothing set for the path to the input layout file (e.g. a *.gds) yet")

    @layout_file.setter
    def layout_file(self, value: str) -> None:
        """Set the path to the input layout file (e.g. a *.gds)."""
        if not (isinstance(value, str)):
            raise TypeError("layout_file must be a str")
        self.attr_setter("_layout_file", value)


    @property
    def schematic_files(self) -> List[str]:
        """
        Get the path to the input SPICE or Verilog schematic files (e.g. *.v or *.spi).

        :return: The path to the input SPICE or Verilog schematic files (e.g. *.v or *.spi).
        """
        try:
            return self.attr_getter("_schematic_files", None)
        except AttributeError:
            raise ValueError("Nothing set for the path to the input SPICE or Verilog schematic files (e.g. *.v or *.spi) yet")

    @schematic_files.setter
    def schematic_files(self, value: List[str]) -> None:
        """Set the path to the input SPICE or Verilog schematic files (e.g. *.v or *.spi)."""
        if not (isinstance(value, List)):
            raise TypeError("schematic_files must be a List[str]")
        self.attr_setter("_schematic_files", value)


    @property
    def hcells_list(self) -> List[str]:
        """
        Get the list of cells to explicitly map hierarchically in LVS.

        :return: The list of cells to explicitly map hierarchically in LVS.
        """
        try:
            return self.attr_getter("_hcells_list", None)
        except AttributeError:
            raise ValueError("Nothing set for the list of cells to explicitly map hierarchically in LVS yet")

    @hcells_list.setter
    def hcells_list(self, value: List[str]) -> None:
        """Set the list of cells to explicitly map hierarchically in LVS."""
        if not (isinstance(value, List)):
            raise TypeError("hcells_list must be a List[str]")
        self.attr_setter("_hcells_list", value)


    @property
    def ilms(self) -> List[ILMStruct]:
        """
        Get the list of (optional) input ILM information for hierarchical mode.

        :return: The list of (optional) input ILM information for hierarchical mode.
        """
        try:
            return self.attr_getter("_ilms", None)
        except AttributeError:
            raise ValueError("Nothing set for the list of (optional) input ILM information for hierarchical mode yet")

    @ilms.setter
    def ilms(self, value: List[ILMStruct]) -> None:
        """Set the list of (optional) input ILM information for hierarchical mode."""
        if not (isinstance(value, List)):
            raise TypeError("ilms must be a List[ILMStruct]")
        self.attr_setter("_ilms", value)


    ### Outputs ###
    ### END Generated interface HammerLVSTool ###

class HasUPFSupport(HammerTool):
    """Mix-in trait with functions useful for tools with UPF style power
    constraints"""
    @property
    def upf_power_specification(self) -> str:
        raise NotImplementedError("Automatic generation of UPF power specifications is not supported yet.")

class HasCPFSupport(HammerTool):
    """Mix-in trait with functions useful for tools with CPF style power
    constraints"""
    @property
    def cpf_power_specification(self) -> str:
        output = [] # type: List[str]
        # Just names
        domain = "AO"
        condition = "nominal"
        mode = "aon"
        # Header
        output.append("set_cpf_version 1.0e")
        output.append("set_hierarchy_separator /")

        output.append("set_design {t}".format(t=self.top_module))
        # Define power and ground nets
        power_nets = self.get_all_power_nets() # type: List[Supply]
        ground_nets = self.get_all_ground_nets() # type: List[Supply]
        vdd = VoltageValue(self.get_setting("vlsi.inputs.supplies.VDD")) # type: VoltageValue
        output.append("create_power_nets -nets {{ {p} }} -voltage {v}".
                format(p=" ".join(map(lambda x: x.name, power_nets)), v=vdd.value))
        output.append("create_ground_nets -nets {{ {g} }}".
                format(g=" ".join(map(lambda x: x.name, ground_nets))))

        # Define power domain and connections
        output.append("create_power_domain -name {d} -default".format(d=domain))
        # Assume primary power are first in list
        output.append("update_power_domain -name {d} -primary_power_net {pp} -primary_ground_net {pg}".
                format(d=domain, pp=power_nets[0].name, pg=ground_nets[0].name))
        # Assuming that all power/ground nets correspond to pins
        for pg_net in (power_nets+ground_nets):
            if(pg_net.pin != None):
                output.append("create_global_connection -domain {d} -net {n} -pins {p}".
                        format(d=domain, n=pg_net.name, p=pg_net.pin))

        # Create nominal operation condtion and power mode
        output.append("create_nominal_condition -name {c} -voltage {v}".
                format(c=condition, v=vdd.value))
        output.append("create_power_mode -name {m} -default -domain_conditions {{{d}@{c}}}".
                format(m=mode, d=domain, c=condition))

        # Footer
        output.append("end_design")

        return "\n".join(output)


class HasSDCSupport(HammerTool):
    """Mix-in trait with functions useful for tools with SDC-style
    constraints."""
    @property
    def sdc_clock_constraints(self) -> str:
        """Generate TCL fragments for top module clock constraints."""
        output = [] # type: List[str]

        clocks = self.get_clock_ports()
        for clock in clocks:
            # TODO: FIXME This assumes that library units are always in ns!!!
            if get_or_else(clock.generated, False):
                output.append("create_generated_clock -name {n} -source {m_path} -divide_by {div} {path}".
                        format(n=clock.name, m_path=clock.source_path, div=clock.divisor, path=clock.path))
            elif clock.path is not None:
                output.append("create_clock {0} -name {1} -period {2}".format(clock.path, clock.name, clock.period.value_in_units("ns")))
            else:
                output.append("create_clock {0} -name {0} -period {1}".format(clock.name, clock.period.value_in_units("ns")))
            if clock.uncertainty is not None:
                output.append("set_clock_uncertainty {1} [get_clocks {0}]".format(clock.name, clock.uncertainty.value_in_units("ns")))

        output.append("\n")
        return "\n".join(output)

    @property
    def sdc_pin_constraints(self) -> str:
        """Generate a fragment for I/O pin constraints."""
        output = []  # type: List[str]

        default_output_load = float(self.get_setting("vlsi.inputs.default_output_load"))

        # Specify default load.
        output.append("set_load {load} [all_outputs]".format(
            load=default_output_load
        ))

        # Also specify loads for specific pins.
        for load in self.get_output_load_constraints():
            output.append("set_load {load} [get_port \"{name}\"]".format(
                load=load.load,
                name=load.name
            ))

        # Also specify delays for specific pins.
        for delay in self.get_delay_constraints():
            output.append("set_{direction}_delay {delay} -clock {clock} [get_port \"{name}\"]".format(
                delay=delay.delay.value_in_units("ns"),
                clock=delay.clock,
                direction=delay.direction,
                name=delay.name
            ))

        # Custom sdc constraints that are verbatim appended
        custom_sdc_constraints = self.get_setting("vlsi.inputs.custom_sdc_constraints")  # type: List[str]
        for custom in custom_sdc_constraints:
            output.append(str(custom))

        return "\n".join(output)

    @property
    @abstractmethod
    def post_synth_sdc(self) -> Optional[str]:
        """
        Get the (optional) input post-synthesis SDC constraint file.

        :return: The (optional) input post-synthesis SDC constraint file.
        """
        pass


class CadenceTool(HasSDCSupport, HasCPFSupport, HasUPFSupport, HammerTool):
    """Mix-in trait with functions useful for Cadence-based tools."""

    @property
    def config_dirs(self) -> List[str]:
        # Override this to pull in Cadence-common configs.
        return [self.get_setting("cadence.common_path")] + super().config_dirs

    @property
    def env_vars(self) -> Dict[str, str]:
        """
        Get the list of environment variables required for this tool.
        Note to subclasses: remember to include variables from super().env_vars!
        """
        # Use the base extra_env_variables and ensure that our custom variables are on top.
        list_of_vars = self.get_setting("cadence.extra_env_vars")  # type: List[Dict[str, Any]]
        assert isinstance(list_of_vars, list)

        cadence_vars = {
            "CDS_LIC_FILE": self.get_setting("cadence.CDS_LIC_FILE"),
            "CADENCE_HOME": self.get_setting("cadence.cadence_home")
        }

        return reduce(add_dicts, [dict(super().env_vars)] + list_of_vars + [cadence_vars], {})

    def version_number(self, version: str) -> int:
        """
        Assumes versions look like MAJOR_ISRMINOR and we will have less than 100 minor versions.
        """
        main_version = int(version.split("_")[0]) # type: int
        minor_version = 0 # type: int
        if "_" in version:
            minor_version = int(version.split("_")[1][3:])
        return main_version * 100 + minor_version

    def get_timing_libs(self, corner: Optional[MMMCCorner] = None) -> str:
        """
        Helper function to get the list of ASCII timing .lib files in space separated format.
        Note that Cadence tools support ECSM, so we can use the ECSM-based filter.

        :param corner: Optional corner to consider. If supplied, this will use filter_for_mmmc to select libraries that
        match a given corner (voltage/temperature).
        :return: List of lib files separated by spaces
        """
        pre_filters = optional_map(corner, lambda c: [self.filter_for_mmmc(voltage=c.voltage,
                                                                           temp=c.temp)])  # type: Optional[List[Callable[[hammer_tech.Library],bool]]]

        lib_args = self.technology.read_libs([hammer_tech.filters.timing_lib_with_ecsm_filter],
                                             hammer_tech.HammerTechnologyUtils.to_plain_item,
                                             extra_pre_filters=pre_filters)
        return " ".join(lib_args)

    def get_mmmc_qrc(self, corner: MMMCCorner) -> str:
        lib_args = self.technology.read_libs([hammer_tech.filters.qrc_tech_filter],
                                             hammer_tech.HammerTechnologyUtils.to_plain_item,
                                             extra_pre_filters=[
                                                 self.filter_for_mmmc(voltage=corner.voltage, temp=corner.temp)])
        return " ".join(lib_args)

    def get_qrc_tech(self) -> str:
        """
        Helper function to get the list of rc corner tech files in space separated format.

        :return: List of qrc tech files separated by spaces
        """
        lib_args = self.technology.read_libs([
            hammer_tech.filters.qrc_tech_filter
        ], hammer_tech.HammerTechnologyUtils.to_plain_item)
        return " ".join(lib_args)

    def generate_mmmc_script(self) -> str:
        """
        Output for the mmmc.tcl script.
        Innovus (init_design) requires that the timing script be placed in a separate file.

        :return: Contents of the mmmc script.
        """
        mmmc_output = []  # type: List[str]

        def append_mmmc(cmd: str) -> None:
            self.verbose_tcl_append(cmd, mmmc_output)

        # Create an Innovus constraint mode.
        constraint_mode = "my_constraint_mode"
        sdc_files = []  # type: List[str]

        # Generate constraints
        clock_constraints_fragment = os.path.join(self.run_dir, "clock_constraints_fragment.sdc")
        with open(clock_constraints_fragment, "w") as f:
            f.write(self.sdc_clock_constraints)
        sdc_files.append(clock_constraints_fragment)

        # Generate port constraints.
        pin_constraints_fragment = os.path.join(self.run_dir, "pin_constraints_fragment.sdc")
        with open(pin_constraints_fragment, "w") as f:
            f.write(self.sdc_pin_constraints)
        sdc_files.append(pin_constraints_fragment)

        # Add the post-synthesis SDC, if present.
        post_synth_sdc = self.post_synth_sdc
        if post_synth_sdc is not None:
            sdc_files.append(post_synth_sdc)

        # TODO: add floorplanning SDC
        if len(sdc_files) > 0:
            sdc_files_arg = "-sdc_files [list {sdc_files}]".format(
                sdc_files=" ".join(sdc_files)
            )
        else:
            blank_sdc = os.path.join(self.run_dir, "blank.sdc")
            self.run_executable(["touch", blank_sdc])
            sdc_files_arg = "-sdc_files {{ {} }}".format(blank_sdc)
        append_mmmc("create_constraint_mode -name {name} {sdc_files_arg}".format(
            name=constraint_mode,
            sdc_files_arg=sdc_files_arg
        ))

        corners = self.get_mmmc_corners()  # type: List[MMMCCorner]
        # In parallel, create the delay corners
        if corners:
            setup_corner = corners[0]  # type: MMMCCorner
            hold_corner = corners[0]  # type: MMMCCorner
            # TODO(colins): handle more than one corner and do something with extra corners
            for corner in corners:
                if corner.type is MMMCCornerType.Setup:
                    setup_corner = corner
                if corner.type is MMMCCornerType.Hold:
                    hold_corner = corner

            # First, create Innovus library sets
            append_mmmc("create_library_set -name {name} -timing [list {list}]".format(
                name="{n}.setup_set".format(n=setup_corner.name),
                list=self.get_timing_libs(setup_corner)
            ))
            append_mmmc("create_library_set -name {name} -timing [list {list}]".format(
                name="{n}.hold_set".format(n=hold_corner.name),
                list=self.get_timing_libs(hold_corner)
            ))
            # Skip opconds for now
            # Next, create Innovus timing conditions
            append_mmmc("create_timing_condition -name {name} -library_sets [list {list}]".format(
                name="{n}.setup_cond".format(n=setup_corner.name),
                list="{n}.setup_set".format(n=setup_corner.name)
            ))
            append_mmmc("create_timing_condition -name {name} -library_sets [list {list}]".format(
                name="{n}.hold_cond".format(n=hold_corner.name),
                list="{n}.hold_set".format(n=hold_corner.name)
            ))
            # Next, create Innovus rc corners from qrc tech files
            append_mmmc("create_rc_corner -name {name} -temperature {tempInCelsius} {qrc}".format(
                name="{n}.setup_rc".format(n=setup_corner.name),
                tempInCelsius=str(setup_corner.temp.value),
                qrc="-qrc_tech {}".format(self.get_mmmc_qrc(setup_corner)) if self.get_mmmc_qrc(setup_corner) != '' else ''
            ))
            append_mmmc("create_rc_corner -name {name} -temperature {tempInCelsius} {qrc}".format(
                name="{n}.hold_rc".format(n=hold_corner.name),
                tempInCelsius=str(hold_corner.temp.value),
                qrc="-qrc_tech {}".format(self.get_mmmc_qrc(hold_corner)) if self.get_mmmc_qrc(hold_corner) != '' else ''
            ))
            # Next, create an Innovus delay corner.
            append_mmmc(
                "create_delay_corner -name {name}_delay -timing_condition {name}_cond -rc_corner {name}_rc".format(
                    name="{n}.setup".format(n=setup_corner.name)
                ))
            append_mmmc(
                "create_delay_corner -name {name}_delay -timing_condition {name}_cond -rc_corner {name}_rc".format(
                    name="{n}.hold".format(n=hold_corner.name)
                ))
            # Next, create the analysis views
            append_mmmc("create_analysis_view -name {name}_view -delay_corner {name}_delay -constraint_mode {constraint}".format(
                name="{n}.setup".format(n=setup_corner.name), constraint=constraint_mode))
            append_mmmc("create_analysis_view -name {name}_view -delay_corner {name}_delay -constraint_mode {constraint}".format(
                name="{n}.hold".format(n=hold_corner.name), constraint=constraint_mode))
            # Finally, apply the analysis view.
            append_mmmc("set_analysis_view -setup {{ {setup_view} }} -hold {{ {hold_view} }}".format(
                setup_view="{n}.setup_view".format(n=setup_corner.name),
                hold_view="{n}.hold_view".format(n=hold_corner.name)
            ))
        else:
            # First, create an Innovus library set.
            library_set_name = "my_lib_set"
            append_mmmc("create_library_set -name {name} -timing [list {list}]".format(
                name=library_set_name,
                list=self.get_timing_libs()
            ))
            # Next, create an Innovus timing condition.
            timing_condition_name = "my_timing_condition"
            append_mmmc("create_timing_condition -name {name} -library_sets [list {list}]".format(
                name=timing_condition_name,
                list=library_set_name
            ))
            # extra junk: -opcond ...
            rc_corner_name = "rc_cond"
            append_mmmc("create_rc_corner -name {name} -temperature {tempInCelsius} {qrc}".format(
                name=rc_corner_name,
                tempInCelsius=120,  # TODO: this should come from tech config
                qrc="-qrc_tech {}".format(self.get_qrc_tech()) if self.get_qrc_tech() != '' else ''
            ))
            # Next, create an Innovus delay corner.
            delay_corner_name = "my_delay_corner"
            append_mmmc(
                "create_delay_corner -name {name} -timing_condition {timing_cond} -rc_corner {rc}".format(
                    name=delay_corner_name,
                    timing_cond=timing_condition_name,
                    rc=rc_corner_name
                ))
            # extra junk: -rc_corner my_rc_corner_maybe_worst
            # Next, create an Innovus analysis view.
            analysis_view_name = "my_view"
            append_mmmc("create_analysis_view -name {name} -delay_corner {corner} -constraint_mode {constraint}".format(
                name=analysis_view_name, corner=delay_corner_name, constraint=constraint_mode))
            # Finally, apply the analysis view.
            # TODO: introduce different views of setup/hold and true multi-corner
            append_mmmc("set_analysis_view -setup {{ {setup_view} }} -hold {{ {hold_view} }}".format(
                setup_view=analysis_view_name,
                hold_view=analysis_view_name
            ))

        return "\n".join(mmmc_output)

    def generate_dont_use_commands(self) -> List[str]:
        """
        Generate a list of dont_use commands for Cadence tools.
        """

        def map_cell(in_cell: str) -> str:
            # "*/" is needed for "get_db lib_cells <cell_expression>"
            if in_cell.startswith("*/"):
                mapped_cell = in_cell  # type: str
            else:
                mapped_cell = "*/" + in_cell

            # Check for cell existence first to avoid Genus erroring out.
            get_db_str = "[get_db lib_cells {mapped_cell}]".format(mapped_cell=mapped_cell)
            # Escaped version for puts.
            get_db_str_escaped = get_db_str.replace('[', '\[').replace(']', '\]')
            return """
puts "set_dont_use {get_db_str_escaped}"
if {{ {get_db_str} ne "" }} {{
    set_dont_use {get_db_str}
}} else {{
    puts "WARNING: cell {mapped_cell} was not found for set_dont_use"
}}
            """.format(get_db_str=get_db_str, get_db_str_escaped=get_db_str_escaped, mapped_cell=mapped_cell)

        return list(map(map_cell, self.get_dont_use_list()))

    def generate_power_spec_commands(self) -> List[str]:
        """
        Generate commands to load a power specification for Cadence tools.
        """

        power_spec_type = str(self.get_setting("vlsi.inputs.power_spec_type"))  # type: str
        power_spec_arg = ""  # type: str
        if power_spec_type == "cpf":
            power_spec_arg = "cpf"
        elif power_spec_type == "upf":
            power_spec_arg = "1801"
        else:
            self.logger.error(
                "Invalid power specification type '{tpe}'; only 'cpf' or 'upf' supported".format(tpe=power_spec_type))
            return []

        power_spec_contents = ""  # type: str
        power_spec_mode = str(self.get_setting("vlsi.inputs.power_spec_mode"))  # type: str
        if power_spec_mode == "empty":
            return []
        elif power_spec_mode == "auto":
            if power_spec_type == "cpf":
                power_spec_contents = self.cpf_power_specification
            elif power_spec_type == "upf":
                power_spec_contents = self.upf_power_specification
        elif power_spec_mode == "manual":
            power_spec_contents = str(self.get_setting("vlsi.inputs.power_spec_contents"))
        else:
            self.logger.error("Invalid power specification mode '{mode}'; using 'empty'.".format(mode=power_spec_mode))
            return []

        # Write the power spec contents to file and include it
        power_spec_file = os.path.join(self.run_dir, "power_spec.{tpe}".format(tpe=power_spec_type))
        with open(power_spec_file, "w") as f:
            f.write(power_spec_contents)
        return ["read_power_intent -{arg} {path}".format(arg=power_spec_arg, path=power_spec_file),
                "commit_power_intent"]


class SynopsysTool(HasSDCSupport, HammerTool):
    """Mix-in trait with functions useful for Synopsys-based tools."""

    @property
    def env_vars(self) -> Dict[str, str]:
        """
        Get the list of environment variables required for this tool.
        Note to subclasses: remember to include variables from super().env_vars!
        """
        result = dict(super().env_vars)
        result.update({
            "SNPSLMD_LICENSE_FILE": self.get_setting("synopsys.SNPSLMD_LICENSE_FILE"),
            # TODO: this is actually a Mentor Graphics licence, not sure why the old dc scripts depend on it.
            "MGLS_LICENSE_FILE": self.get_setting("synopsys.MGLS_LICENSE_FILE")
        })
        return result

    def version_number(self, version: str) -> int:
        """
        Assumes versions look like NAME-YYYY.MM-SPMINOR.
        Assumes less than 100 minor versions.
        """
        date = "-".join(version.split("-")[1:])  # type: str
        year = int(date.split(".")[0])  # type: int
        month = int(date.split(".")[1][:2])  # type: int
        minor_version = 0  # type: int
        if "-" in date:
            minor_version = int(date.split("-")[1][2:])
        return (year * 100 + month) * 100 + minor_version

    def get_synopsys_rm_tarball(self, product: str, settings_key: str = "") -> str:
        """Locate reference methodology tarball.

        :param product: Either "DC" or "ICC"
        :param settings_key: Key to retrieve the version for the product. Leave blank for DC and ICC.
        """
        key = self.tool_config_prefix() + "." + "version" # type: str

        synopsys_rm_tarball = os.path.join(self.get_setting("synopsys.rm_dir"), "%s-RM_%s.tar" % (product, self.get_setting(key)))
        if not os.path.exists(synopsys_rm_tarball):
            # TODO: convert these to logger calls
            raise FileNotFoundError("Expected reference methodology tarball not found at %s. Use the Synopsys RM generator <https://solvnet.synopsys.com/rmgen> to generate a DC reference methodology. If these tarballs have been pre-downloaded, you can set synopsys.rm_dir instead of generating them yourself." % (synopsys_rm_tarball))
        else:
            return synopsys_rm_tarball

class MentorTool(HammerTool):
    """ Mix-in trait with functions useful for Mentor-Graphics-based tools. """

    @property
    def config_dirs(self) -> List[str]:
        # Override this to pull in Mentor-common configs.
        return [self.get_setting("mentor.common_path")] + super().config_dirs

    @property
    def env_vars(self) -> Dict[str, str]:
        """
        Get the list of environment variables required for this tool.
        Note to subclasses: remember to include variables from super().env_vars!
        """
        # Use the base extra_env_variables and ensure that our custom variables are on top.
        list_of_vars = self.get_setting("mentor.extra_env_vars")  # type: List[Dict[str, Any]]
        assert isinstance(list_of_vars, list)

        mentor_vars = {
            "MGLS_LICENSE_FILE": self.get_setting("mentor.MGLS_LICENSE_FILE"),
            "MENTOR_HOME": self.get_setting("mentor.mentor_home")
        }

        return reduce(add_dicts, [dict(super().env_vars)] + list_of_vars + [mentor_vars], {})

    def version_number(self, version: str) -> int:
        """
        Assumes versions look like NAME-YYYY.MM-SPMINOR.
        Assumes less than 100 minor versions.
        """
        # TODO(johnwright)
        # We currently do not support Calibre versions
        return 0

class MentorCalibreTool(MentorTool):
    """ Mix-in trait for Mentor's Calibre tool suite. """
    @property
    def env_vars(self) -> Dict[str, str]:
        """
        Get the list of environment variables required for this tool.
        Note to subclasses: remember to include variables from super().env_vars!
        """
        return super().env_vars


def load_tool(tool_name: str, path: Iterable[str]) -> HammerTool:
    """
    Load the given tool.
    See the hammer-vlsi README for how it works.

    :param tool_name: Name of the tool
    :param path: List of paths to get
    :return: HammerTool of the given tool
    """
    # Temporarily add to the import path.
    for p in path:
        sys.path.insert(0, p)
    try:
        # import_module loads/caches modules into sys.modules, so if
        # another module with the same name (but different sys.path) is loaded,
        # import_module won't look in sys.path again.
        # We need to remove this module from sys.modules to get import_module
        # to load the modules from sys.path.
        # See https://docs.python.org/3/library/importlib.html
        if tool_name in sys.modules:
            del sys.modules[tool_name]
        mod = importlib.import_module(tool_name)
    except ImportError:
        raise ValueError("No such tool " + tool_name)
    # Now restore the original import path.
    for _ in path:
        sys.path.pop(0)
    try:
        tool_class = getattr(mod, "tool")
    except AttributeError:
        raise ValueError("No such tool " + tool_name + ", or tool does not follow the hammer-vlsi tool library format")

    if not issubclass(tool_class, HammerTool):
        raise ValueError("Tool must be a HammerTool")

    # Set the tool directory.
    tool = tool_class()
    tool.tool_dir = os.path.dirname(os.path.abspath(mod.__file__))
    return tool

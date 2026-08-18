#!/usr/bin/env python3
# File   : event_engine.launch.py
# Purpose: Start the event engine with the shared YAML parameters.

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    pkg_share = get_package_share_directory("scout2map_event")
    default_params = os.path.join(pkg_share, "config", "event_engine.yaml")

    params_arg = DeclareLaunchArgument(
        "params_file", default_value=default_params,
        description="YAML file with event_engine parameters")

    engine = Node(
        package="scout2map_event",
        executable="event_engine",
        name="event_engine",
        output="screen",
        emulate_tty=True,
        parameters=[LaunchConfiguration("params_file")],
    )

    return LaunchDescription([params_arg, engine])

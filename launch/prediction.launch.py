#!/usr/bin/env python3
# File   : prediction.launch.py
# Purpose: Run the trend prediction node against the stored sensor history.

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    args = [
        DeclareLaunchArgument("horizon_s", default_value="60.0"),
        DeclareLaunchArgument("update_period_s", default_value="5.0"),
        DeclareLaunchArgument("min_samples", default_value="20"),
        DeclareLaunchArgument("min_span_s", default_value="30.0"),
        DeclareLaunchArgument("db_path", default_value=""),
    ]

    prediction = Node(
        package="scout2map_event",
        executable="prediction_node",
        name="prediction_node",
        output="screen",
        emulate_tty=True,
        parameters=[{
            "horizon_s": LaunchConfiguration("horizon_s"),
            "update_period_s": LaunchConfiguration("update_period_s"),
            "min_samples": LaunchConfiguration("min_samples"),
            "min_span_s": LaunchConfiguration("min_span_s"),
            "db_path": LaunchConfiguration("db_path"),
        }],
    )

    return LaunchDescription(args + [prediction])

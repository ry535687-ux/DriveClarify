#!/bin/sh
# 仅设置非基准bootstrap world；官方evaluator随后load_world指定原路线Town。
exec /home/buaa/CARLA_0.9.15/CarlaUE4.sh /Game/Carla/Maps/Town01 "$@"
